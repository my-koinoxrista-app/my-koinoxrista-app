import streamlit as st
from Pdf_Generator import create_pdf
from rag_assistant import ask_rag, load_models

# Page Configuration
st.set_page_config(page_title="Building Management App", layout="wide")

# 1. LOAD MODELS (Cached)
@st.cache_resource
def get_models():
    return load_models()

# Προσαρμογή: Unpacking των vectorstore & chat_llm
vectorstore, chat_llm = get_models()

# 2. SESSION STATE FOR CHAT
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {
            "role": "system", 
            "content": (
                "Είσαι ένας χρήσιμος βοηθός διαχείρισης κτηρίου. "
                "Απάντησε στην ερώτηση του χρήστη αποκλειστικά και μόνο με βάση τις πληροφορίες που σου δίνονται, στα Ελληνικά."
            )
        }
    ]

# --- UI LAYOUT ---
st.title("🏢 Διαχείριση Κοινοχρήστων & RAG Assistant")

col1, col2 = st.columns([1, 1])

# ΑΡΙΣΤΕΡΗ ΣΤΗΛΗ: Υπολογισμός & Έκδοση PDF
with col1:
    st.header("📄 Έκδοση Κοινοχρήστων")
    with st.form("data_form"):
        period = st.text_input("Περίοδος", "16/02/26 - 15/03/26")
        reuma = st.number_input("Σύνολο Ρεύματος (€)", min_value=0.0, format="%.2f")
        nero = st.number_input("Σύνολο Νερού (€)", min_value=0.0, format="%.2f")
        episkeves = st.number_input("Σύνολο Επισκευών (€)", min_value=0.0, format="%.2f")
        
        # Νέο πεδίο για την περιγραφή της βλάβης/επισκευής
        repair_name = st.text_input(
            "Περιγραφή Επισκευής (στα Λατινικά/Αγγλικά)", 
            value="General",
            help="π.χ. Plumbing, Roof Fix, Door Lock"
        )
        
        submit_button = st.form_submit_button("Υπολογισμός")

    if submit_button:
        # Κλήση της create_pdf με την προσθήκη του repair_name
        pdf_bytes = create_pdf(period, reuma, nero, episkeves, repair_name)
        st.success("Το PDF δημιουργήθηκε επιτυχώς!")

        st.download_button(
            label="📥 Λήψη PDF",
            data=bytes(pdf_bytes),
            file_name=f"koinoxrista_{period.replace('/', '-')}.pdf",
            mime="application/pdf"
        )

# ΔΕΞΙΑ ΣΤΗΛΗ: RAG Chatbot
with col2:
    st.header("🤖 Βοηθός RAG")
    
    # Προβολή Ιστορικού (αγνοώντας το system message)
    for msg in st.session_state.chat_history:
        if msg["role"] == "system":
            continue
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    # Chat Input
    if user_input := st.chat_input("Ρωτήστε για τους κανόνες κοινοχρήστων..."):
        # 1. Εμφάνιση & Αποθήκευση μηνύματος χρήστη
        with st.chat_message("user"):
            st.write(user_input)
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        
        # 2. Παραγωγή Απάντησης από το RAG
        with st.chat_message("assistant"):
            with st.spinner("Ανάλυση..."):
                response = ask_rag(
                    user_query=user_input,
                    history=st.session_state.chat_history,
                    vectorstore=vectorstore,
                    chat_llm=chat_llm
                )
                st.write(response)
        
        # 3. Αποθήκευση απάντησης βοηθού στο ιστορικό
        st.session_state.chat_history.append({"role": "assistant", "content": response})