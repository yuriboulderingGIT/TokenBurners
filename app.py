"""Streamlit demo. Run: streamlit run app.py"""

import streamlit as st

from src.llm import ask
from src.voice import speak

# Change this to set how the AI behaves
SYSTEM_PROMPT = "You are a helpful assistant. Keep answers short and clear."

st.set_page_config(page_title="TokenBurners demo")
st.title("TokenBurners demo")

user_input = st.text_area("Your question")
read_aloud = st.checkbox("Read the answer aloud")

if st.button("Ask the AI", type="primary"):
    if not user_input.strip():
        st.warning("Please type something first.")
    else:
        try:
            with st.spinner("Thinking..."):
                answer = ask(user_input, system=SYSTEM_PROMPT)
            st.write(answer)

            if read_aloud:
                with st.spinner("Making audio..."):
                    audio = speak(answer)
                st.audio(audio, format="audio/mp3")
        except Exception as e:
            st.error(f"Something went wrong: {e}")
