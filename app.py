import streamlit as st
import pandas as pd
import json
import requests
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="Cotizador B2B", layout="centered")
st.title("Cotizador IA - B2B")

# Cache del servicio para no refactorizar la conexión en cada clic
@st.cache_resource
def get_drive_service():
    credenciales_gcp = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        credenciales_gcp, 
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

# Cache de los DataFrames (1 hora de TTL) para evitar agotar la API de Drive
@st.cache_data(ttl=3600)
def descargar_excel(_drive_service, file_id):
    request = _drive_service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_stream.seek(0)
    return pd.read_excel(file_stream)

@st.cache_data(ttl=3600)
def cargar_bases():
    drive = get_drive_service()
    df_precios = descargar_excel(drive, st.secrets["ID_LISTA_PRECIOS"])
    df_correcciones = descargar_excel(drive, st.secrets["ID_CORRECCIONES"])
    df_marcas = descargar_excel(drive, st.secrets["ID_MARCAS"])
    return df_precios, df_correcciones, df_marcas

def llamar_llm_gemini(texto_cliente):
    api_key = st.secrets["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    prompt = f"""
    Eres un extractor de datos técnicos de materiales eléctricos.
    Analiza esta solicitud: '{texto_cliente}'
    Devuelve ÚNICAMENTE un JSON con esta estructura:
    {{"producto": "nombre generico", "atributos": ["atr1", "atr2"], "marca": "marca si existe o null"}}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        return json.loads(response.json()['candidates'][0]['content']['parts'][0]['text'])
    else:
        return None

def procesar_cotizacion(texto_cliente, df_precios, df_correcciones, df_marcas):
    # 1. Bypass determinista
    bypass = df_correcciones[df_correcciones['Texto Cliente'].str.lower() == texto_cliente.lower()]
    if not bypass.empty:
        return bypass.iloc[0]['Codigo Oficial JIELI']
    
    # 2. Extracción IA
    datos = llamar_llm_gemini(texto_cliente)
    if not datos:
        return "ERROR_LLM"
    
    producto = datos.get('producto', '')
    marca_solicitada = datos.get('marca', '')
    
    # 3. Fallback en texto sucio (Hasta que normalices la columna Marca)
    candidatos = df_precios[df_precios['Detalle'].str.contains(producto, case=False, na=False)]
    if marca_solicitada:
        candidatos = candidatos[candidatos['Detalle'].str.contains(marca_solicitada, case=False, na=False)]
    
    if candidatos.empty:
        return "SIN_COINCIDENCIAS"
    
    return candidatos.iloc[0]['Código']

# --- Interfaz Visual ---
try:
    df_precios, df_correcciones, df_marcas = cargar_bases()
    st.success("Bases de datos sincronizadas.", icon="✅")
except Exception as e:
    st.error(f"Error de conexión con Drive: {e}")
    st.stop()

st.markdown("---")
solicitud = st.text_input("Solicitud del cliente:")

if st.button("Buscar SKU", type="primary"):
    if solicitud:
        with st.spinner("Procesando matriz..."):
            codigo = procesar_cotizacion(solicitud, df_precios, df_correcciones, df_marcas)
            if codigo in ["ERROR_LLM", "SIN_COINCIDENCIAS"]:
                st.error(f"Resultado: {codigo}")
            else:
                st.metric(label="SKU Asignado", value=codigo)
    else:
        st.warning("Falta el input de búsqueda.")
