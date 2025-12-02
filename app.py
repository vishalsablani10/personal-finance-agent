@st.cache_data(ttl=600)
def analyze_market_news(ticker_df):
    news_insights = []
    st.header("📰 News Sentiment Analysis")
    
    if 'GROQ_API_KEY' not in st.secrets:
        return []
    
    # Check if Ticker DF is valid/not empty
    if ticker_df.empty:
        st.info("No assets found in Ticker sheet for news analysis.")
        return []

    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    
    for index, row in ticker_df.iterrows():
        ticker_symbol = row['Ticker']
        # Ticker sheet uses 'Asset' column, Watchlist used 'Asset_Name'
        asset_name = row.get('Asset', row.get('Asset_Name', 'Unknown Asset'))
        
        try:
            ticker_obj = yf.Ticker(ticker_symbol)
            news_list = ticker_obj.news
            
            # --- UPDATED LOGIC FOR MISSING NEWS ---
            if not news_list:
                st.warning(f"I couldn't find any news for {asset_name}.")
                continue
                
            headlines = [item.get('title', '') for item in news_list[:3]]
            headlines_text = "\n".join(headlines)
            
            if not headlines_text:
                st.warning(f"I couldn't find any news for {asset_name}.")
                continue
            # --------------------------------------

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
        
    # Scout market dips (Price logic still depends on Watchlist threshold)
    if not watchlist_df.empty:
        with st.spinner("Scouting market for opportunities..."):
            market_insights = check_market_dips(watchlist_df) 
    
    # Analyze News (Now uses Ticker Sheet for broader coverage)
    if not ticker_map_df.empty:
        with st.spinner("Analyzing news sentiment..."):
            news_insights = analyze_market_news(ticker_map_df)
    
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
