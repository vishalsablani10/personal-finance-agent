# This is a new file: run_agent.py
# This script is NOT a Streamlit app. It's a simple Python script
# designed to be run on a schedule (e.g., by GitHub Actions).

import gspread
import pandas as pd
from googleapiclient.discovery import build
from google.oauth2 import service_account
import os
from groq import Groq
import yfinance as yf
import datetime as dt
from twilio.rest import Client 
import sys 
import json 
import time # Added for safety (delaying API calls)

# --- Load Secrets from Environment Variables ---
try:
    # Google Credentials
    google_creds_json = os.environ['GOOGLE_CREDS_JSON']
    google_creds_dict = json.loads(google_creds_json)
    
    # Twilio Credentials
    twilio_sid = os.environ['TWILIO_ACCOUNT_SID']
    twilio_token = os.environ['TWILIO_AUTH_TOKEN']
    twilio_phone = os.environ['TWILIO_PHONE_NUMBER']
    my_phone = os.environ['MY_PHONE_NUMBER']
    
    # Groq Credentials
    groq_api_key = os.environ['GROQ_API_KEY']
    
except KeyError as e:
    print(f"CRITICAL ERROR: Environment variable {e} not set.")
    print("Please set all required secrets in your GitHub Actions settings.")
    sys.exit(1)

# --- Google API Authentication (for backend) ---
SCOPES_SHEETS = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def get_gsheet_client():
    """Connect to Google Sheets API."""
    try:
        creds = gspread.service_account_from_dict(google_creds_dict, scopes=SCOPES_SHEETS)
        return creds
    except Exception as e:
        print(f"An error occurred connecting to Google Sheets: {e}")
        return None

# --- LLM "Communicator" Function (UPDATED for News) ---
def get_llm_summary(rebalance_insights, market_insights, news_insights):
    """
    Takes lists of all insights and gets a single human-friendly summary from Groq.
    """
    if not rebalance_insights and not market_insights and not news_insights:
        return "No specific insights to summarize today. All systems normal."
        
    try:
        client = Groq(api_key=groq_api_key) 
        
        insights_text = "Internal Portfolio Alerts:\n" + "\n".join(rebalance_insights)
        insights_text += "\n\nExternal Market Opportunities:\n" + "\n".join(market_insights)
        insights_text += "\n\nRecent News Sentiment:\n" + "\n".join(news_insights)
        
        system_prompt = (
            "You are a concise and clear-spoken personal finance assistant. "
            "I will give you three lists of alerts: 1) Internal Portfolio Alerts (rebalancing needs), "
            "2) External Market Opportunities (assets on sale), and 3) Recent News Sentiment. "
            "Your job is to summarize them in a single, professional, and actionable paragraph. "
            "Prioritize ALERTs and OPPORTUNITIEs first. Then mention significant news (Positive/Negative). "
            "Be brief. This is for a WhatsApp message. Use newlines for readability. "
            "Sign off with '- Your Finance Agent'."
        )
        
        user_prompt = f"Here are today's portfolio alerts:\n{insights_text}"
        
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
        print(f"Error connecting to Groq API: {e}")
        return None

# --- NEW: News Analysis Function ---

def analyze_market_news(watchlist_df, groq_api_key):
    """
    Simulates fetching relevant news for each ticker and uses Groq to generate a sentiment insight.
    """
    news_insights = []
    
    # We will iterate through each watchlist asset and search for recent news.
    for index, row in watchlist_df.iterrows():
        ticker = row['Ticker']
        asset_name = row['Asset_Name']
        
        # 1. Simulate Google Search (Using the search tool for live results)
        # Note: The actual tool call is handled by the Gemini backend, 
        # so here we format the prompt correctly for Groq to receive the search data.
        
        search_query = f"Latest 24 hours news and analyst reports for {asset_name} ({ticker})"
        
        # This function definition relies on the LLM performing a grounded search.
        # We will wrap the Groq call to analyze the search results.
        
        try:
            client = Groq(api_key=groq_api_key)
            
            system_prompt = (
                f"You are a financial news summarizer. A user is asking for sentiment analysis on {asset_name} ({ticker}) "
                f"based on the latest news articles provided via Google Search results. "
                f"Your output must be a single, concise sentence in this format: "
                f"SENTIMENT: [Asset Name] is [Sentiment: POSITIVE/NEGATIVE/NEUTRAL] due to [Specific reason, max 10 words]."
            )

            # Use the search tool through Groq
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": search_query}
                ],
                model="llama-3.1-8b-instant",
                temperature=0.5,
                tools=[{"google_search": {}}] # Enable Google Search Grounding
            )
            
            # Extract and clean the sentiment summary
            sentiment_summary = chat_completion.choices[0].message.content.strip()
            
            if sentiment_summary.startswith("SENTIMENT:"):
                news_insights.append(sentiment_summary)
            else:
                news_insights.append(f"NEWS: Could not determine clear sentiment for {asset_name}.")

            print(f"  > News Analysis for {asset_name}: {sentiment_summary}")
            
            # Introduce a small delay to avoid hitting API rate limits for rapid consecutive calls
            time.sleep(1) 
            
        except Exception as e:
            print(f"Error during news sentiment analysis for {asset_name}: {e}")
            news_insights.append(f"NEWS: Failed to analyze recent news for {asset_name}.")
            
    return news_insights


# --- DATA LOADING (Unchanged) ---
# (load_portfolio, load_rules_from_sheet, load_watchlist functions remain the same as in app.py)
def load_portfolio(_client, sheet_name):
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Portfolio")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        df['Current_Value'] = pd.to_numeric(df['Current_Value'])
        return df
    except Exception as e:
        print(f"Error loading 'Portfolio' tab: {e}")
        return pd.DataFrame()

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
        print(f"Error loading 'Rules' tab: {e}")
        return pd.DataFrame()

def load_watchlist(_client, sheet_name):
    if not _client: return pd.DataFrame()
    try:
        sheet = _client.open(sheet_name).worksheet("Watchlist")
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        df['Dip_Threshold_Percent'] = pd.to_numeric(df['Dip_Threshold_Percent'])
        return df
    except Exception as e:
        print(f"Error loading 'Watchlist' tab: {e}")
        return pd.DataFrame()


# --- ANALYST FUNCTIONS (Rebalance and Dip Check remain the same) ---
def generate_rebalance_insights(portfolio_df, rules_df):
    """Checks for internal portfolio allocation drift."""
    insight_messages = [] 
    if portfolio_df.empty or rules_df.empty:
        return []
    try:
        print("Analyzing portfolio rebalancing...")
        category_df = portfolio_df.groupby('Category')['Current_Value'].sum().reset_index()
        total_value = category_df['Current_Value'].sum()
        category_df['Current_Percentage'] = (category_df['Current_Value'] / total_value) * 100
        merged_df = pd.merge(rules_df, category_df, on='Category', how='left')
        merged_df['Current_Percentage'] = merged_df['Current_Percentage'].fillna(0)
        merged_df['Drift'] = merged_df['Current_Percentage'] - merged_df['Target_Percentage']
        merged_df['Is_Alert'] = abs(merged_df['Drift']) > merged_df['Rebalance_Threshold']
        
        for _, row in merged_df.iterrows():
            if row['Is_Alert']:
                curr_perc = row['Current_Percentage']
                target_perc = row['Target_Percentage']
                drift = row['Drift']
                threshold = row['Rebalance_Threshold']
                status = "over-allocated" if drift > 0 else "under-allocated"
                message = (
                    f"ALERT: Your '{row['Category']}' allocation is {curr_perc:.1f}% "
                    f"(Target: {target_perc}%). This is {abs(drift):.1f}% {status} and "
                    f"outside your {threshold}% threshold."
                )
                print(f"  > Insight: {message}")
                insight_messages.append(message)
        return insight_messages
    except Exception as e:
        print(f"An error occurred while generating rebalancing insights: {e}")
        return []

def check_market_dips(watchlist_df):
    """Checks for external market buying opportunities."""
    insight_messages = []
    if watchlist_df.empty:
        return []
    
    print("Scouting market for opportunities...")
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
                print(f"  > Warning: Could not get data for {asset_name} ({ticker_symbol}).")
                continue

            clean_high = data['High'].dropna()
            clean_close = data['Close'].dropna()

            if clean_high.empty or clean_close.empty:
                print(f"  > Warning: No valid price data for {asset_name} ({ticker_symbol}).")
                continue

            high_52_week = clean_high.max()
            current_price = clean_close.iloc[-1]
            percent_from_high = ((current_price - high_52_week) / high_52_week) * 100
            
            if abs(percent_from_high) > threshold:
                message = (
                    f"OPPORTUNITY: {asset_name} ({ticker_symbol}) is {abs(percent_from_high):.1f}% "
                    f"below its 52-week high (Current: ${current_price:,.2f}, High: ${high_52_week:,.2f}). "
                    f"This is past your {threshold}% threshold."
                )
                print(f"  > Insight: {message}")
                insight_messages.append(message)
            else:
                print(f"  > OK: {asset_name} is within threshold.")
                
        except Exception as e:
            print(f"An error occurred while checking market dip for {row['Asset_Name']}: {e}")
            
    return insight_messages


# --- NEW: Twilio Message Function (Unchanged) ---
def send_whatsapp_message(body):
    """Sends a WhatsApp message using Twilio."""
    try:
        client = Client(twilio_sid, twilio_token)
        message = client.messages.create(
            from_=f'whatsapp:{twilio_phone}',
            body=body,
            to=f'whatsapp:{my_phone}'
        )
        print(f"Message sent successfully! SID: {message.sid}")
        return True
    except Exception as e:
        print(f"Error sending Twilio message: {e}")
        return False

# --- Main Execution (UPDATED) ---
def main():
    print("--- Personal Finance Agent: Daily Run ---")
    
    G_SHEET_NAME = "Investment_Analysis"
    
    # 1. Connect
    gsheet_client = get_gsheet_client()
    if not gsheet_client:
        print("CRITICAL: Could not connect to Google Sheets. Exiting.")
        return

    # 2. Load all data
    print("Loading data from Google Sheets...")
    portfolio_df = load_portfolio(gsheet_client, G_SHEET_NAME)
    rules_df = load_rules_from_sheet(gsheet_client, G_SHEET_NAME)
    watchlist_df = load_watchlist(gsheet_client, G_SHEET_NAME)
    
    # 3. Generate all insights
    rebalance_insights = generate_rebalance_insights(portfolio_df, rules_df)
    market_insights = check_market_dips(watchlist_df)
    news_insights = analyze_market_news(watchlist_df, groq_api_key) # <-- NEW CALL
    
    # 4. Generate AI Summary
    if not rebalance_insights and not market_insights and not news_insights: # <-- UPDATED CHECK
        print("No new insights today. No message will be sent.")
        return

    print("Generating AI summary...")
    # Pass all three lists
    summary = get_llm_summary(rebalance_insights, market_insights, news_insights) 
    
    if not summary:
        print("CRITICAL: Failed to generate AI summary. Exiting.")
        return
        
    print(f"Final Summary:\n{summary}")
    
    # 5. Send WhatsApp Message
    print("Sending summary to WhatsApp...")
    send_whatsapp_message(summary)
    
    print("--- Agent run complete. ---")

if __name__ == "__main__":
    main()
