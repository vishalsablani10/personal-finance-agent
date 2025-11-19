import streamlit as st
import os
from groq import Groq
import json

# --- Groq Chat Function ---

def get_chat_response(prompt, history):
    """
    Calls the Groq API to get a response, using history for context.
    """
    if 'GROQ_API_KEY' not in st.secrets:
        return "The Groq API key is missing. Please check your Streamlit secrets."
        
    try:
        client = Groq(api_key=st.secrets["GROQ_API_KEY"])
        
        # System instructions to define the chatbot's persona
        system_instruction = (
            "You are a friendly, knowledgeable, and professional Personal Finance Advisor "
            "named 'Finance Bot'. You must answer questions concisely and provide actionable "
            "or educational advice. Do not mention your underlying model or code. "
            "Keep responses conversational and helpful."
        )

        # Convert Streamlit history format to Groq API message format
        messages = [{"role": "system", "content": system_instruction}]
        
        # Add past conversation history
        for msg in history:
            role = "assistant" if msg["role"] == "assistant" else "user"
            messages.append({"role": role, "content": msg["content"]})
        
        # Add the current user prompt
        messages.append({"role": "user", "content": prompt})

        chat_completion = client.chat.completions.create(
            messages=messages,
            model="llama-3.1-8b-instant",
            temperature=0.8,
        )
        
        return chat_completion.choices[0].message.content
        
    except Exception as e:
        return f"Error communicating with the LLM: {e}"

# --- Main Chat UI Function ---

def render_chat_tab():
    """Renders the entire chatbot interface."""
    st.title("💬 Your Financial Advisor Chatbot")
    st.markdown("Ask the bot about finance, market concepts, or your portfolio rules.")

    # Initialize chat history in session_state
    if "messages" not in st.session_state or st.session_state.get('chat_initialized', False) is False:
        st.session_state["messages"] = [{"role": "assistant", "content": "Hello! I am Finance Bot. How can I help you analyze your portfolio or navigate the markets today?"}]
        st.session_state['chat_initialized'] = True

    # Display chat messages from history
    for msg in st.session_state.messages:
        # Check if the message is the initialization message to avoid erroring if we reuse the tab
        if msg.get('content') and msg['content'].strip(): 
            st.chat_message(msg["role"]).write(msg["content"])

    # Handle user input
    if prompt := st.chat_input("Ask a finance question..."):
        
        # 1. Display user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.chat_message("user").write(prompt)
        
        # 2. Get and display assistant response
        with st.chat_message("assistant"):
            with st.spinner("Finance Bot is thinking..."):
                # Call Groq API
                response = get_chat_response(prompt, st.session_state.messages)
            
            # Write response to the chat
            st.write(response)
            
            # 3. Store assistant response
            st.session_state.messages.append({"role": "assistant", "content": response})
