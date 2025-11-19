import streamlit as st
import gspread
import pandas as pd
import plotly.express as px
from googleapiclient.discovery import build
from google.oauth2 import service_account
import os
from groq import Groq
import base64 
import json   
import yfinance as yf
import datetime as dt
import time 

# --- Import the Chat Tab ---
# Ensure chat_tab.py exists in the same folder
from chat_tab import render_chat_tab
# ---------------------------

# --- Page Configuration ---
st.set_page_config(
    page_title="Personal Finance Agent",
    page_icon="🤖",
    layout="wide",
)

# --- Global Constants ---
G_DOC_ID = "1o_ACMebYAXB_i7eox1qX23OYYMrF2mbOFNC7RTa75Fo"
G_SHEET_NAME = "Investment_Analysis"
SCOPES_SHEETS = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SCOPES_DOCS = ['https://www.googleapis.com/auth/drive'] 

# --- AUTH & UTILITY FUNCTIONS ---

@st.cache_resource
def get_creds_dict():
    """Helper function to load credentials by decoding Base64 string from secrets."""
    if 'GOOGLE_BASE64_CREDS' in st.secrets:
        try:
            encoded_json = st.secrets['GOOGLE_BASE64_CREDS']
            decoded_bytes = base64.b64decode(encoded_json)
            return json.loads(decoded_bytes)
        except Exception as e:
            st.error(f"Error decoding Google credentials from Base64: {e}")
            return None
    elif os.path.exists('credentials.json'):
        return 'credentials.json'
    else:
        st.error("Could not find GOOGLE_BASE64_CREDS in Streamlit secrets.")
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
            # FIXED: Initialize credentials then apply scopes
            doc_creds = service_account.Credentials.from_service_account_info(creds_source)
            scoped_creds = doc_creds.with_scopes(SCOPES_DOCS)
            service = build('docs', 'v1', credentials=scoped_creds)
        else:
            doc_creds = service_account.Credentials.from_service_account_file(creds_source, scopes=SCOPES_DOCS)
            service = build('docs', 'v1', credentials=doc_creds)
            
        return service
    except Exception as e:
        st.error(f"Error loading Google Doc: {e}")
        return None

# --- DATA LOADING ---

@st.cache_data(ttl=600)
def load_portfolio(_client, sheet_name):
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Portfolio")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
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
        df['Target_Percentage'] = pd.to_numeric(df['Target_Percentage'])
        df['Rebalance_Threshold'] = pd.to_numeric(df['Rebalance_Threshold'])
        return df
    except Exception as e:
        st.error(f"Error loading 'Rules' tab: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def load_watchlist(_client, sheet_name):
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Watchlist")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        df['Dip_Threshold_Percent'] = pd.to_numeric(df['Dip_Threshold_Percent'])
        return df
    except Exception as e:
        st.error(f"Error loading 'Watchlist' tab: {e}")
        return pd.DataFrame()

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

# --- INTELLIGENT ANALYST FUNCTIONS ---

@st.cache_data(ttl=600)
def get_llm_summary(rebalance_insights, market_insights, news_insights):
    """Generates the main dashboard summary using Groq."""
    if not rebalance_insights and not market_insights and not news_insights:
        return "No specific insights to summarize today. All systems normal."
        
    if 'GROQ_API_KEY' not in st.secrets:
        return "GROQ_API_KEY not found in Streamlit secrets. Cannot generate summary."
        
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        insights_text = "Internal Portfolio Alerts:\n" + "\n".join(rebalance_insights)
        insights_text += "\n\nExternal Market Opportunities:\n" + "\n".join(market_insights)
        insights_text += "\n\nRecent News Sentiment:\n" + "\n".join(news_insights) 
        
        system_prompt = (
            "You are a concise and clear-spoken personal finance assistant. "
            "I will give you three lists of alerts: 1) Internal Portfolio Alerts, "
            "2) External Market Opportunities, and 3) Recent News Sentiment. "
            "Summarize them into a single, professional, actionable paragraph. "
            "Prioritize Alerts and Opportunities. "
            "Be brief (3-4 sentences max). Do not use markdown or bullet points."
        )
        user_prompt = f"Here are today's alerts:\n{insights_text}"
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama-3.1-8b-instant",
            temperature=0.7,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        st.error(f"Error connecting to Groq API: {e}")
        return None

@st.cache_data(ttl=600)
def analyze_market_news(watchlist_df):
    """
    Fetches real news via yfinance and uses Groq for sentiment analysis.
    """
    news_insights = []
    st.header("📰 News Sentiment Analysis")
    
    if 'GROQ_API_KEY' not in st.secrets:
        st.error("Cannot run news analysis: GROQ_API_KEY missing.")
        return []

    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    
    for index, row in watchlist_df.iterrows():
        ticker_symbol = row['Ticker']
        asset_name = row['Asset_Name']
        
        try:
            # 1. Get News from yfinance (Free, no new API key needed)
            ticker_obj = yf.Ticker(ticker_symbol)
            news_list = ticker_obj.news
            
            if not news_list:
                st.warning(f"No recent news found for {asset_name}.")
                continue
                
            # 2. Prepare Headlines for Groq
            headlines = [item.get('title', '') for item in news_list[:3]] # Get top 3 headlines
            headlines_text = "\n".join(headlines)
            
            if not headlines_text:
                continue

            # 3. Analyze Sentiment with Groq
            system_prompt = (
                f"Analyze the sentiment for {asset_name} ({ticker_symbol}) based on these headlines. "
                f"Output a SINGLE sentence in this format: "
                f"SENTIMENT: [Asset Name] is [POSITIVE/NEGATIVE/NEUTRAL] due to [Brief Reason]."
            )
            
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Headlines:\n{headlines_text}"}
                ],
                model="llama-3.1-8b-instant",
                temperature=0.5,
            )
            
            sentiment_summary = chat_completion.choices[0].message.content.strip()
            
            # 4. Display and Store Result
            if "SENTIMENT:" in sentiment_summary:
                # Color coding
                if 'POSITIVE' in sentiment_summary.upper():
                    st.success(f"**{sentiment_summary}**")
                elif 'NEGATIVE' in sentiment_summary.upper():
                    st.error(f"**{sentiment_summary}**")
                else:
                    st.info(f"**{sentiment_summary}**")
                news_insights.append(sentiment_summary)
            else:
                st.write(f"**{asset_name}:** {sentiment_summary}")
                news_insights.append(f"News for {asset_name}: {sentiment_summary}")

            time.sleep(0.5) # Rate limit protection
            
        except Exception as e:
            st.warning(f"Could not analyze news for {asset_name}: {e}")
            
    return news_insights

def generate_rebalance_insights(portfolio_df, rules_df):
    """Checks for internal portfolio allocation drift."""
    insight_messages = [] 
    if portfolio_df.empty or rules_df.empty:
        return []
    try:
        st.header("🤖 Agent Insights (Rebalancing)")
        category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
        total_value = category_df['Current_Value'].sum()
        category_df['Current_Percentage'] = (category_df['Current_Value'] / total_value) * 100
        merged_df = pd.merge(rules_df, category_df, on='Category', how='left')
        merged_df['Current_Percentage'] = merged_df['Current_Percentage'].fillna(0)
        merged_df['Drift'] = merged_df['Current_Percentage'] - merged_df['Target_Percentage']
        merged_df['Is_Alert'] = abs(merged_df['Drift']) > merged_df['Rebalance_Threshold']
        
        for _, row in merged_df.iterrows():
            curr_perc = row['Current_Percentage']
            target_perc = row['Target_Percentage']
            drift = row['Drift']
            threshold = row['Rebalance_Threshold']
            if row['Is_Alert']:
                status = "over-allocated" if drift > 0 else "under-allocated"
                message = f"ALERT: Your '{row['Category']}' is {curr_perc:.1f}% (Target: {target_perc}%). {abs(drift):.1f}% {status}."
                st.error(f"**{message}**")
                insight_messages.append(message)
            else:
                message = f"OK: '{row['Category']}' is {curr_perc:.1f}% (Within threshold)."
                st.success(f"**{message}**")
        return insight_messages
    except Exception as e:
        st.error(f"Rebalancing check error: {e}")
        return []

@st.cache_data(ttl=600)
def check_market_dips(watchlist_df):
    """Checks for external market buying opportunities."""
    insight_messages = []
    if watchlist_df.empty:
        return []
    
    st.header("📈 Market Opportunities (Scout)")
    today = dt.date.today()
    one_year_ago = today - dt.timedelta(days=365)
    
    for _, row in watchlist_df.iterrows():
        try:
            ticker_symbol = row['Ticker']
            asset_name = row['Asset_Name']
            threshold = row['Dip_Threshold_Percent']
            
            ticker_obj = yf.Ticker(ticker_symbol)
            data = ticker_obj.history(start=one_year_ago, end=today)
            
            if data.empty:
                continue

            clean_close = data['Close'].dropna()
            if clean_close.empty:
                continue

            high_52_week = data['High'].max()
            current_price = clean_close.iloc[-1]
            percent_from_high = ((current_price - high_52_week) / high_52_week) * 100
            
            if abs(percent_from_high) > threshold:
                message = f"OPPORTUNITY: {asset_name} is {abs(percent_from_high):.1f}% below 52-week high."
                st.info(f"**{message}**") 
                insight_messages.append(message)
            else:
                message = f"OK: {asset_name} is {abs(percent_from_high):.1f}% below high."
                st.success(f"**{message}**")
                
        except Exception as e:
            st.error(f"Market Scout error for {row['Asset_Name']}: {e}")
            
    return insight_messages

# --- DASHBOARD RENDERER ---

def render_dashboard_tab(gsheet_client, gdoc_service):
    st.title("🤖 Personal Finance Agent Dashboard")
    
    with st.spinner("Loading all financial data..."):
        portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME)
        rules_df = load_rules_from_sheet(gsheet_client, G_SHEET_NAME)
        watchlist_df = load_watchlist(gsheet_client, G_SHEET_NAME) 
    
    rebalance_insights = []
    market_insights = []
    news_insights = [] 
    
    if not portfolio_df.empty and not rules_df.empty:
        rebalance_insights = generate_rebalance_insights(portfolio_df, rules_df)
        
    if not watchlist_df.empty:
        with st.spinner("Scouting market for opportunities..."):
            market_insights = check_market_dips(watchlist_df) 
        
        # --- NEWS INTEGRATION ---
        with st.spinner("Analyzing news sentiment (yfinance + Groq)..."):
            news_insights = analyze_market_news(watchlist_df)
    
    st.divider()
    st.header("💡 Agent's Combined Summary")
    if rebalance_insights or market_insights or news_insights: 
        with st.spinner("Generating AI summary..."):
            summary = get_llm_summary(rebalance_insights, market_insights, news_insights) 
            if summary:
                st.info(f"**{summary}**")
    else:
        st.success("All systems normal. No new alerts.")
    
    st.divider()
    
    st.header("💰 Current Portfolio Allocation")
    if not portfolio_df.empty:
        total_value = portfolio_df['Current_Value'].sum()
        st.subheader(f"Total Portfolio Value: ${total_value:,.2f}")
        col1, col2 = st.columns(2)
        with col1:
            fig_asset = px.pie(portfolio_df, names='Asset', values='Current_Value', title='By Asset', hole=0.3)
            st.plotly_chart(fig_asset, use_container_width=True)
        with col2:
            category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
            fig_category = px.pie(category_df, names='Category', values='Current_Value', title='By Category', hole=0.3)
            st.plotly_chart(fig_category, use_container_width=True)
        st.dataframe(portfolio_df)

    st.divider()
    st.header("📜 My Investment Principles")
    with st.spinner("Loading principles..."):
        rules_text = load_rules_from_doc(gdoc_service, G_DOC_ID) 
        if rules_text:
            st.markdown(rules_text)
        else:
            st.warning("Could not load principles. Check Doc ID and permissions.")

# --- MAIN ---

def main():
    gsheet_client = get_gsheet_client()
    gdoc_service = get_gdoc_service()

    st.sidebar.title("Agent Control")
    st.sidebar.info("Runs daily at 9 AM IST.")
    
    if gsheet_client is None and gdoc_service is None:
        st.error("FATAL ERROR: Client initialization failed. Check Secrets.")
        return

    tab1, tab2 = st.tabs(["📊 Dashboard & Alerts", "💬 Advisor Chat"])
    
    with tab1:
        render_dashboard_tab(gsheet_client, gdoc_service)
    
    with tab2:
        render_chat_tab()

if __name__ == "__main__":
    main()
