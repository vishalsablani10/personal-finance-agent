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
    creds_source = get_creds_dict()
    if creds_source is None: return None
    try:
        if isinstance(creds_source, dict):
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
    """
    Loads 'Transactions' sheet and groups by Asset/Category to create a Portfolio view.
    """
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Transactions")
        data = sheet.get_all_records()
        raw_df = pd.DataFrame(data)
        
        required_cols = ['Asset', 'Category', 'Invested Value (Rs)']
        if not all(col in raw_df.columns for col in required_cols):
            st.error(f"Error: 'Transactions' sheet must have columns: {', '.join(required_cols)}")
            return pd.DataFrame()

        raw_df['Invested Value (Rs)'] = pd.to_numeric(
            raw_df['Invested Value (Rs)'].astype(str).str.replace(',', ''), errors='coerce'
        ).fillna(0)
        
        # Clean: Remove rows where Asset name is empty or Value is 0
        raw_df = raw_df[raw_df['Asset'].astype(str).str.strip() != '']
        raw_df = raw_df[raw_df['Invested Value (Rs)'] > 0]

        portfolio_df = raw_df.groupby(['Asset', 'Category'])[['Invested Value (Rs)']].sum().reset_index()
        portfolio_df.rename(columns={'Invested Value (Rs)': 'Current_Value'}, inplace=True)

        return portfolio_df
        
    except Exception as e:
        st.error(f"Error processing 'Transactions' tab: {e}")
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
def load_ticker_map(_client, sheet_name):
    """Loads the explicit Asset -> Ticker mapping from the 'Ticker' sheet."""
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Ticker")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        if 'Asset' not in df.columns or 'Ticker' not in df.columns:
            st.error("Error: 'Ticker' sheet must have columns 'Asset' and 'Ticker'.")
            return pd.DataFrame()
        return df
    except Exception as e:
        st.warning(f"Could not load 'Ticker' tab. Performance table may be empty. Error: {e}")
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
    if not rebalance_insights and not market_insights and not news_insights:
        return "No specific insights to summarize today. All systems normal."
        
    if 'GROQ_API_KEY' not in st.secrets:
        return "GROQ_API_KEY not found in Streamlit secrets."
        
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        insights_text = "Internal Portfolio Alerts:\n" + "\n".join(rebalance_insights)
        insights_text += "\n\nExternal Market Opportunities:\n" + "\n".join(market_insights)
        insights_text += "\n\nRecent News Sentiment:\n" + "\n".join(news_insights) 
        
        system_prompt = (
            "You are a concise personal finance assistant. "
            "Summarize these alerts: 1) Portfolio Alerts, 2) Market Opportunities, 3) News Sentiment. "
            "Prioritize Alerts. Be brief (3-4 sentences). No markdown."
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
    news_insights = []
    st.header("📰 News Sentiment Analysis")
    
    if 'GROQ_API_KEY' not in st.secrets:
        return []

    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    
    for index, row in watchlist_df.iterrows():
        ticker_symbol = row['Ticker']
        asset_name = row['Asset_Name']
        
        try:
            ticker_obj = yf.Ticker(ticker_symbol)
            news_list = ticker_obj.news
            if not news_list: continue
                
            headlines = [item.get('title', '') for item in news_list[:3]]
            headlines_text = "\n".join(headlines)
            if not headlines_text: continue

            system_prompt = (
                f"Analyze sentiment for {asset_name} ({ticker_symbol}). "
                f"Output SINGLE sentence format: "
                f"SENTIMENT: [Asset] is [POSITIVE/NEGATIVE/NEUTRAL] due to [Reason]."
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
            
            if "SENTIMENT:" in sentiment_summary:
                if 'POSITIVE' in sentiment_summary.upper():
                    st.success(f"**{sentiment_summary}**")
                elif 'NEGATIVE' in sentiment_summary.upper():
                    st.error(f"**{sentiment_summary}**")
                else:
                    st.info(f"**{sentiment_summary}**")
                news_insights.append(sentiment_summary)
            else:
                st.write(f"**{asset_name}:** {sentiment_summary}")
                news_insights.append(sentiment_summary)

            time.sleep(0.5) 
            
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
    insight_messages = []
    if watchlist_df.empty: return []
    
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
            
            if data.empty: continue
            clean_close = data['Close'].dropna()
            if clean_close.empty: continue

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

# --- UPDATED FUNCTION FOR ROBUST PERFORMANCE CALCULATION ---
@st.cache_data(ttl=3600)
def get_asset_performance(portfolio_df, ticker_df):
    """
    Calculates percentage change using Date-Based Lookups.
    This fixes issues with weekends/holidays by finding the price
    on or immediately before the target date, rather than counting rows.
    """
    performance_data = []
    
    if ticker_df.empty:
        return pd.DataFrame()

    asset_to_ticker = dict(zip(
        ticker_df['Asset'].astype(str).str.strip(), 
        ticker_df['Ticker'].astype(str).str.strip()
    ))
    
    unique_assets = portfolio_df['Asset'].unique()
    
    for asset in unique_assets:
        clean_asset = str(asset).strip()
        ticker = asset_to_ticker.get(clean_asset)
        
        row_data = {
            'Asset': asset,
            'Ticker': ticker if ticker else "Not Found",
            '1D %': 'NA', '1W %': 'NA', '1M %': 'NA', 
            '3M %': 'NA', '6M %': 'NA', '1Y %': 'NA'
        }
        
        if ticker and ticker.lower() != 'nan' and ticker != '':
            try:
                # Fetch 1 year of data
                t = yf.Ticker(ticker)
                hist = t.history(period='1y') 
                
                # Ensure data exists and clean index
                if not hist.empty and len(hist) > 1:
                    hist.index = pd.to_datetime(hist.index).tz_localize(None)
                    
                    # Current price (last available close)
                    current_price = hist['Close'].iloc[-1]
                    current_date = hist.index[-1]
                    
                    # --- 1. Calculate 1D Change (Last Close vs Prev Close) ---
                    prev_close = hist['Close'].iloc[-2]
                    change_1d = ((current_price - prev_close) / prev_close) * 100
                    row_data['1D %'] = f"{change_1d:+.2f}%"

                    # --- 2. Calculate Dates for Other Timeframes ---
                    timeframes = {
                        '1W %': 7,
                        '1M %': 30,
                        '3M %': 90,
                        '6M %': 180,
                        '1Y %': 365
                    }
                    
                    for label, days_back in timeframes.items():
                        target_date = current_date - dt.timedelta(days=days_back)
                        
                        # Find the last trading day on or before the target date
                        # This automatically handles weekends and holidays
                        past_data = hist[hist.index <= target_date]
                        
                        if not past_data.empty:
                            ref_price = past_data['Close'].iloc[-1]
                            pct_change = ((current_price - ref_price) / ref_price) * 100
                            row_data[label] = f"{pct_change:+.2f}%"
                        else:
                            row_data[label] = "NA" # Not enough history

            except Exception as e:
                print(f"Error fetching data for {asset}: {e}")
                
        performance_data.append(row_data)
        
    return pd.DataFrame(performance_data)

# --- DASHBOARD RENDERER ---

def render_dashboard_tab(gsheet_client, gdoc_service):
    st.title("🤖 Personal Finance Agent Dashboard")
    
    with st.spinner("Loading and aggregating transaction data..."):
        portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME)
        rules_df = load_rules_from_sheet(gsheet_client, G_SHEET_NAME)
        watchlist_df = load_watchlist(gsheet_client, G_SHEET_NAME) 
        ticker_map_df = load_ticker_map(gsheet_client, G_SHEET_NAME) 
    
    rebalance_insights = []
    market_insights = []
    news_insights = [] 
    
    if not portfolio_df.empty and not rules_df.empty:
        rebalance_insights = generate_rebalance_insights(portfolio_df, rules_df)
        
    if not watchlist_df.empty:
        with st.spinner("Scouting market for opportunities..."):
            market_insights = check_market_dips(watchlist_df) 
        
        with st.spinner("Analyzing news sentiment..."):
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
    
    st.header("💰 Current Portfolio Allocation (Based on Invested Value)")
    if not portfolio_df.empty:
        total_value = portfolio_df['Current_Value'].sum()
        st.subheader(f"Total Invested Value: Rs {total_value:,.2f}")
        col1, col2 = st.columns(2)
        
        with col1:
            fig_asset = px.pie(portfolio_df, names='Asset', values='Current_Value', title='By Asset', hole=0.3)
            st.plotly_chart(fig_asset, use_container_width=True)
            
        with col2:
            category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
            
            if not rules_df.empty:
                category_df = pd.merge(category_df, rules_df[['Category', 'Target_Percentage']], on='Category', how='left')
                category_df['Target_Percentage'] = category_df['Target_Percentage'].fillna(0)
            
            fig_category = px.pie(
                category_df, 
                names='Category', 
                values='Current_Value', 
                title='By Category', 
                hole=0.3,
                hover_data=['Target_Percentage'] 
            )
            fig_category.update_traces(
                hovertemplate="<b>%{label}</b><br>Value: %{value}<br>Current: %{percent}<br>Target: %{customdata[0]}%"
            )
            st.plotly_chart(fig_category, use_container_width=True)
            
        display_df = portfolio_df.copy()
        display_df.rename(columns={'Current_Value': 'Invested_Value'}, inplace=True)
        st.dataframe(display_df, hide_index=True)

        # --- UPDATED: Market Performance Table ---
        if not ticker_map_df.empty:
            st.divider()
            st.header("🚀 Market Performance Snapshot")
            st.markdown("Percentage change calculated using 'Ticker' sheet mapping.")
            
            with st.spinner("Calculating market performance metrics..."):
                perf_df = get_asset_performance(portfolio_df, ticker_map_df) 
                if not perf_df.empty:
                    st.dataframe(perf_df, hide_index=True)
        elif not watchlist_df.empty:
             st.warning("Could not load 'Ticker' sheet. Please ensure it exists to see performance metrics.")

    st.divider()
    st.header("📜 My Investment Principles")
    with st.spinner("Loading principles..."):
        rules_text = load_rules_from_doc(gdoc_service, G_DOC_ID) 
        if rules_text:
            st.markdown(rules_text)

# --- MAIN ---

def main():
    gsheet_client = get_gsheet_client()
    gdoc_service = get_gdoc_service()

    st.sidebar.title("Agent Control")
    st.sidebar.info("Runs daily at 9 AM IST.")
    
    if gsheet_client is None and gdoc_service is None:
        st.error("FATAL ERROR: Client initialization failed.")
        return

    tab1, tab2 = st.tabs(["📊 Dashboard & Alerts", "💬 Advisor Chat"])
    
    with tab1:
        render_dashboard_tab(gsheet_client, gdoc_service)
    
    with tab2:
        render_chat_tab()

if __name__ == "__main__":
    main()
