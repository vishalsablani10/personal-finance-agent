import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from googleapiclient.discovery import build
from google.oauth2 import service_account
import os
from groq import Groq  # --- NEW: Import Groq ---

# --- Page Configuration ---
st.set_page_config(
    page_title="Personal Finance Agent",
    page_icon="🤖",
    layout="wide",
)

# --- Google API Authentication (ISOLATED METHOD) ---
SCOPES_SHEETS = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SCOPES_DOCS = ['https://www.googleapis.com/auth/drive']

def get_creds_dict():
    """Helper function to load credentials from secrets or file."""
    if 'google_creds' in st.secrets:
        return dict(st.secrets["google_creds"])
    elif os.path.exists('credentials.json'):
        return 'credentials.json'
    else:
        st.error("Could not find credentials in Streamlit secrets or as 'credentials.json' file.")
        return None

@st.cache_resource
def get_gsheet_client():
    """Connect to Google Sheets API."""
    creds_source = get_creds_dict()
    if creds_source is None: return None
    try:
        if isinstance(creds_source, dict):
            creds = gspread.service_account_from_dict(creds_source, scopes=SCOPES_SHEETS)
        else:
            creds = gspread.service_account(filename=creds_source, scopes=SCOPES_SHEETS)
        return creds
    except Exception as e:
        st.error(f"An error occurred connecting to Google Sheets: {e}")
        return None

@st.cache_resource
def get_gdoc_service():
    """Connect to Google Docs API."""
    creds_source = get_creds_dict()
    if creds_source is None: return None
    try:
        if isinstance(creds_source, dict):
            doc_creds = service_account.Credentials.from_service_account_info(creds_source, scopes=SCOPES_DOCS)
        else:
            doc_creds = service_account.Credentials.from_service_account_file(creds_source, scopes=SCOPES_DOCS)
        service = build('docs', 'v1', credentials=doc_creds)
        return service
    except Exception as e:
        st.error(f"An error occurred connecting to Google Docs: {e}")
        return None

# --- NEW: LLM "Communicator" Function ---
@st.cache_data(ttl=600)
def get_llm_summary(insights_list):
    """
    Takes a list of insight strings and gets a human-friendly summary from Groq.
    """
    if not insights_list:
        return "No specific insights to summarize today."
        
    # Check for Groq API key in secrets
    if 'GROQ_API_KEY' not in st.secrets:
        st.error("GROQ_API_KEY not found in Streamlit secrets. Cannot generate summary.")
        return None
        
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # Combine insights into a single string for the prompt
        insights_text = "\n".join(insights_list)
        
        system_prompt = (
            "You are a concise and clear-spoken personal finance assistant. "
            "I will give you a list of portfolio alerts. Your job is to summarize them "
            "in a human-friendly, professional, and actionable way. "
            "Start with the most important alert (e.g., 'ALERT:') first. "
            "Be brief (2-3 sentences max). Do not use markdown or bullet points, "
            "just write a clean paragraph."
        )
        
        user_prompt = f"Here are today's portfolio alerts:\n{insights_text}"
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama3-8b-8192", # Fast and capable model
            temperature=0.7,
        )
        
        return chat_completion.choices[0].message.content
        
    except Exception as e:
        st.error(f"Error connecting to Groq API: {e}")
        return None

# --- Data Loading Functions (Unchanged) ---
@st.cache_data(ttl=600)
def load_rules_from_doc(_doc_service, document_id):
    if not _doc_service: return None
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
    if not _client: return pd.DataFrame()
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
    if not _client: return pd.DataFrame()
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
    insight_messages = [] 
    if portfolio_df.empty or rules_df.empty:
        return []
    try:
        category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
        total_value = category_df['Current_Value'].sum()
        category_df['Current_Percentage'] = (category_df['Current_Value'] / total_value) * 100
        merged_df = pd.merge(rules_df, category_df, on='Category', how='left')
        merged_df['Current_Percentage'] = merged_df['Current_Percentage'].fillna(0)
        merged_df['Drift'] = merged_df['Current_Percentage'] - merged_df['Target_Percentage']
        merged_df['Is_Alert'] = abs(merged_df['Drift']) > merged_df['Rebalance_Threshold']
        
        st.header("🤖 Agent Insights (Detailed View)") # Renamed header
        for _, row in merged_df.iterrows():
            curr_perc = row['Current_Percentage']
            target_perc = row['Target_Percentage']
            drift = row['Drift']
            threshold = row['Rebalance_Threshold']
            
            if row['Is_Alert']:
                status = "over-allocated" if drift > 0 else "under-allocated"
                message = (
                    f"ALERT: Your '{row['Category']}' allocation is {curr_perc:.1f}% "
                    f"(Target: {target_perc}%). This is {abs(drift):.1f}% {status} and "
                    f"outside your {threshold}% threshold."
                )
                st.error(f"**{message}**") # Show the message
                insight_messages.append(message) # Add raw text to list
            else:
                message = (
                    f"OK: Your '{row['Category']}' allocation is {curr_perc:.1f}% "
                    f"(Target: {target_perc}%). This is within your {threshold}% threshold."
                )
                st.success(f"**{message}**")
                insight_messages.append(message) 
        return insight_messages
    except Exception as e:
        st.error(f"An error occurred while generating insights: {e}")
        return []

# --- Main Application ---
st.title("🤖 Personal Finance Agent")

G_DOC_ID = "1o_ACMebYAXB_i7eox1qX23OYYMrF2mbOFNC7RTa75Fo"
G_SHEET_NAME = "Investment_Analysis"

# Authenticate
gsheet_client = get_gsheet_client()
gdoc_service = get_gdoc_service()

if gsheet_client and gdoc_service:
    
    with st.spinner("Loading portfolio and rules from Google Sheet..."):
        portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME)
        rules_df = load_rules_from_sheet(gsheet_client, G_SHEET_NAME)
    
    # --- UPDATED: Generate LLM Summary First ---
    if not portfolio_df.empty and not rules_df.empty:
        
        # 1. Get the raw insight list
        insights_list = generate_insights(portfolio_df, rules_df)
        
        # 2. Get the LLM summary
        if insights_list: # Only run if there are insights
            st.header("💡 Agent Summary")
            with st.spinner("Generating AI summary..."):
                summary = get_llm_summary(insights_list)
                if summary:
                    st.info(summary)
        
    else:
        st.warning("Could not generate insights. Check portfolio and rules data in your Google Sheet.")
    
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
        
    st.divider()
    
    # --- 2. Investment Rules (from Google Doc) ---
    st.header("📜 My Investment Principles")
    with st.spinner("Loading principles from Google Doc..."):
        rules_text = load_rules_from_doc(gdoc_service, G_DOC_ID) 
        if rules_text:
            st.markdown(rules_text)
        else:
            st.warning("Could not load principles. Check Doc ID and sharing permissions.")

else:
    st.error("Authentication failed. Please check your credentials (local file or Streamlit secrets).")
