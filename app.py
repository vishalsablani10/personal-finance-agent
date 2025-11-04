import streamlit as st
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
from googleapiclient.discovery import build
from google.oauth2 import service_account
import os

# --- Page Configuration ---
st.set_page_config(
    page_title="Personal Finance Dashboard",
    page_icon="📊",
    layout="wide",
)

# --- Google API Authentication ---
# Define the scope for the APIs
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# --- NEW: Secret Handling ---
def get_google_creds_dict():
    """
    Check if running in Streamlit Cloud. If so, use Secrets.
    Otherwise, load the local 'credentials.json' file.
    """
    if 'google_creds' in st.secrets:
        # Running in Streamlit Cloud
        st.info("Using Streamlit secrets for Google credentials.")
        # --- THIS IS THE FIX ---
        # Convert the Streamlit AttrDict to a standard Python dict
        return dict(st.secrets["google_creds"])
    else:
        # Running locally
        st.info("Using local 'credentials.json' file.")
        if not os.path.exists('credentials.json'):
            st.error("Local 'credentials.json' file not found.")
            return None
        return 'credentials.json'

@st.cache_resource
def get_gsheet_client():
    """Connect to Google Sheets API and cache the client object."""
    creds_source = get_google_creds_dict()
    if creds_source is None:
        return None
        
    try:
        if isinstance(creds_source, dict):
            # Load from Streamlit secrets (dictionary)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_source, SCOPES)
        else:
            # Load from local file (string path)
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_source, SCOPES)
        
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"An error occurred during Sheets authentication: {e}")
        return None

@st.cache_resource
def get_gdoc_service():
    """Connect to Google Docs API and cache the service object."""
    creds_source = get_google_creds_dict()
    if creds_source is None:
        return None

    try:
        if isinstance(creds_source, dict):
            # Load from Streamlit secrets (dictionary)
            doc_creds = service_account.Credentials.from_service_account_info(creds_source, scopes=SCOPES)
        else:
            # Load from local file (string path)
            doc_creds = service_account.Credentials.from_service_account_file(creds_source, scopes=SCOPES)
        
        service = build('docs', 'v1', credentials=doc_creds)
        return service
    except Exception as e:
        st.error(f"An error occurred during Docs authentication: {e}")
        return None

# --- Data Loading Functions ---
@st.cache_data(ttl=600)
def load_rules(_doc_service, document_id):
    """Fetches and displays content from a Google Doc."""
    if not _doc_service:
        st.warning("Google Doc service is not available.")
        return None
    try:
        document = _doc_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        
        rules_text = ""
        for value in content:
            if 'paragraph' in value:
                elements = value.get('paragraph').get('elements')
                for elem in elements:
                    if 'textRun' in elem:
                        rules_text += elem.get('textRun').get('content')
        return rules_text
    except Exception as e:
        st.error(f"Error loading Google Doc: {e}")
        st.info("Please ensure the Google Doc ID is correct and you have shared the doc with your service account's email.")
        return None

@st.cache_data(ttl=600)
def load_portfolio(_client, sheet_name):
    """Fetches and processes data from a Google Sheet."""
    if not _client:
        st.warning("Google Sheet client is not available.")
        return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Portfolio")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        if 'Current_Value' not in df.columns:
            st.error("Error: 'Current_Value' column not found in 'Portfolio' sheet.")
            return pd.DataFrame()
            
        df['Current_Value'] = pd.to_numeric(df['Current_Value'])
        return df
    except Exception as e:
        st.error(f"Error loading Google Sheet: {e}")
        st.info(f"Please ensure your Google Sheet is named '{sheet_name}', has a tab named 'Portfolio', and is shared with your service account's email.")
        return pd.DataFrame()

# --- Main Application ---
st.title("📊 Personal Finance Agent Dashboard")

G_DOC_ID = st.text_input("Enter your Google Doc ID:", "YOUR_GOOGLE_DOC_ID_HERE")
G_SHEET_NAME = st.text_input("Enter your Google Sheet Name (File Name):", "My Finance Sheet")

gsheet_client = get_gsheet_client()
gdoc_service = get_gdoc_service()

if gsheet_client and gdoc_service:
    
    # --- 1. Investment Rules (from Google Doc) ---
    st.header("📜 My Investment Rules & Principles")
    if G_DOC_ID != "YOUR_GOOGLE_DOC_ID_HERE":
        with st.spinner("Loading rules from Google Doc..."):
            rules = load_rules(gdoc_service, G_DOC_ID) 
            if rules:
                st.markdown(rules)
            else:
                st.warning("Could not load rules. Check Doc ID and sharing permissions.")
    else:
        st.info("Please enter your Google Doc ID above to load your rules.")

    
    st.divider()

    # --- 2. Portfolio Allocation (from Google Sheet) ---
    st.header("💰 Current Portfolio Allocation")
    if G_SHEET_NAME != "My Finance Sheet":
        with st.spinner("Loading portfolio from Google Sheet..."):
            portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME) 

            if not portfolio_df.empty:
                total_value = portfolio_df['Current_Value'].sum()
                portfolio_df['Percentage'] = (portfolio_df['Current_Value'] / total_value)

                st.subheader(f"Total Portfolio Value: ${total_value:,.2f}")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("Allocation by Asset")
                    fig_asset = px.pie(
                        portfolio_df, 
                        names='Asset', 
                        values='Current_Value',
                        title='Allocation by Asset',
                        hole=0.3
                    )
                    fig_asset.update_traces(textposition='inside', textinfo='percent+label')
                    st.plotly_chart(fig_asset, use_container_width=True)

                with col2:
                    st.subheader("Allocation by Category")
                    category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
                    fig_category = px.pie(
                        category_df,
                        names='Category',
                        values='Current_Value',
                        title='Allocation by Category',
                        hole=0.3
                    )
                    fig_category.update_traces(textposition='inside', textinfo='percent+label')
                    st.plotly_chart(fig_category, use_container_width=True)
                
                st.divider()
                st.subheader("Raw Portfolio Data")
                st.dataframe(portfolio_df)
            
            else:
                st.warning("Could not load portfolio data. Check Sheet name, tab name ('Portfolio'), and sharing permissions.")
    else:
        st.info("Please enter your Google Sheet name above to load your portfolio.")

else:
    st.error("Authentication failed. Please check your credentials (local file or Streamlit secrets).")
