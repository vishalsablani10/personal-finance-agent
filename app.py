import streamlit as st
import gspread
import pandas as pd
# We no longer import 'oauth2client'
import plotly.express as px
from googleapiclient.discovery import build
from google.oauth2 import service_account
import os

# --- Page Configuration ---
st.set_page_config(
    page_title="Personal Finance Dashboard",
    page_icon="🤖",
    layout="wide",
)

# --- Google API Authentication (NEW UNIFIED METHOD) ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https.www.googleapis.com/auth/drive'
]

@st.cache_resource
def get_google_creds():
    """
    Loads Google credentials from Streamlit secrets or local file.
    Uses only the 'google.oauth2.service_account' library.
    """
    try:
        if 'google_creds' in st.secrets:
            # Load from Streamlit secrets
            creds_dict = dict(st.secrets["google_creds"])
            creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        else:
            # Load from local 'credentials.json' file
            creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return creds
    except FileNotFoundError:
        st.error("Local 'credentials.json' file not found.")
        return None
    except Exception as e:
        st.error(f"An error occurred during authentication: {e}")
        return None

@st.cache_resource
def get_gsheet_client():
    """Connect to Google Sheets API using the unified credentials."""
    creds = get_google_creds()
    if creds is None:
        return None
    try:
        # Authorize gspread using the unified credentials
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"An error occurred connecting to Google Sheets: {e}")
        return None

@st.cache_resource
def get_gdoc_service():
    """Connect to Google Docs API using the unified credentials."""
    creds = get_google_creds()
    if creds is None:
        return None
    try:
        # Build the Docs service using the unified credentials
        service = build('docs', 'v1', credentials=creds)
        return service
    except Exception as e:
        st.error(f"An error occurred connecting to Google Docs: {e}")
        return None

# --- Data Loading Functions (Unchanged) ---
@st.cache_data(ttl=600)
def load_rules_from_doc(_doc_service, document_id):
    """Fetches text content from a Google Doc."""
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
        return None

@st.cache_data(ttl=600)
def load_portfolio(_client, sheet_name):
    """Fetches portfolio data from the 'Portfolio' tab."""
    if not _client:
        st.warning("Google Sheet client is not available.")
        return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Portfolio")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if 'Current_Value' not in df.columns or 'Category' not in df.columns:
            st.error("Error: 'Portfolio' sheet must have 'Current_Value' and 'Category' columns.")
            return pd.DataFrame()
        df['Current_Value'] = pd.to_numeric(df['Current_Value'])
        return df
    except Exception as e:
        st.error(f"Error loading 'Portfolio' tab: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def load_rules_from_sheet(_client, sheet_name):
    """Fetches allocation rules from the 'Rules' tab."""
    if not _client:
        st.warning("Google Sheet client is not available.")
        return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Rules")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        required_cols = ['Category', 'Target_Percentage', 'Rebalance_Threshold']
        if not all(col in df.columns for col in required_cols):
            st.error(f"Error: 'Rules' sheet must have columns: {', '.join(required_cols)}")
            return pd.DataFrame()
            
        df['Target_Percentage'] = pd.to_numeric(df['Target_Percentage'])
        df['Rebalance_Threshold'] = pd.to_numeric(df['Rebalance_Threshold'])
        return df
    except Exception as e:
        st.error(f"Error loading 'Rules' tab: {e}")
        return pd.DataFrame()

# --- Analyst Function (Unchanged) ---
def generate_insights(portfolio_df, rules_df):
    """Compares portfolio to rules and generates insight messages."""
    
    insight_messages = [] 
    
    if portfolio_df.empty or rules_df.empty:
        return # Don't run if data is missing
        
    try:
        # 1. Calculate current allocations
        category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
        total_value = category_df['Current_Value'].sum()
        category_df['Current_Percentage'] = (category_df['Current_Value'] / total_value) * 100
        
        # 2. Merge with rules
        merged_df = pd.merge(rules_df, category_df, on='Category', how='left')
        merged_df['Current_Percentage'] = merged_df['Current_Percentage'].fillna(0) # Handle categories you own 0 of
        
        # 3. Calculate drift and check for alerts
        merged_df['Drift'] = merged_df['Current_Percentage'] - merged_df['Target_Percentage']
        merged_df['Is_Alert'] = abs(merged_df['Drift']) > merged_df['Rebalance_Threshold']
        
        st.header("🤖 Agent Insights")
        
        # 4. Display insights
        for _, row in merged_df.iterrows():
            curr_perc = row['Current_Percentage']
            target_perc = row['Target_Percentage']
            drift = row['Drift']
            threshold = row['Rebalance_Threshold']
            
            if row['Is_Alert']:
                status = "over-allocated" if drift > 0 else "under-allocated"
                message = (
                    f"**ALERT:** Your **'{row['Category']}'** allocation is **{curr_perc:.1f}%** "
                    f"(Target: {target_perc}%). This is {abs(drift):.1f}% {status} and "
                    f"outside your {threshold}% threshold."
                )
                st.error(message)
                insight_messages.append(message) # Add to list for LLM
            else:
                message = (
                    f"**OK:** Your **'{row['Category']}'** allocation is **{curr_perc:.1f}%** "
                    f"(Target: {target_perc}%). This is within your {threshold}% threshold."
                )
                st.success(message)
                insight_messages.append(message) 
                
        return insight_messages # Return the list of messages
                
    except Exception as e:
        st.error(f"An error occurred while generating insights: {e}")
        return []


# --- Main Application ---
st.title("📊 Personal Finance Agent Dashboard")

G_DOC_ID = "1o_ACMebYAXB_i7eox1qX23OYYMrF2mbOFNC7RTa75Fo"
G_SHEET_NAME = "Investment_Analysis"

# Authenticate
gsheet_client = get_gsheet_client()
gdoc_service = get_gdoc_service()

if gsheet_client and gdoc_service:
    
    # --- Load data first ---
    with st.spinner("Loading portfolio and rules from Google Sheet..."):
        portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME)
        rules_df = load_rules_from_sheet(gsheet_client, G_SHEET_NAME)
    
    # --- 1. Analyst Insights Section ---
    if not portfolio_df.empty and not rules_df.empty:
        insights = generate_insights(portfolio_df, rules_df)
    else:
        st.warning("Could not generate insights. Check portfolio and rules data in your Google Sheet.")
    
    st.divider()

    # --- 2. Investment Rules (from Google Doc) ---
    st.header("📜 My Investment Principles")
    with st.spinner("Loading principles from Google Doc..."):
        rules_text = load_rules_from_doc(gdoc_service, G_DOC_ID) 
        if rules_text:
            st.markdown(rules_text)
        else:
            st.warning("Could not load principles. Check Doc ID and sharing permissions.")
    
    st.divider()

    # --- 3. Portfolio Allocation (from Google Sheet) ---
    st.header("💰 Current Portfolio Allocation")
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
        st.info("Could not load portfolio data. Check 'Portfolio' tab in your Google Sheet.")

else:
    st.error("Authentication failed. Please check your credentials (local file or Streamlit secrets).")
