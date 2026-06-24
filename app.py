import streamlit as st
import pandas as pd
import json
import requests
import io
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="Cotizador B2B", layout="centered")
st.title("Cotizador IA - B2B")

# --- Conexión y Descarga de Drive ---
@st.cache_resource
def get_drive_service():
    credenciales_gcp = st.secrets["gcp_service_account"]
    creds = service_account.Credentials.from_service_account_info(
        credenciales_gcp, 
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

@st.cache_data(ttl=3600)
def descargar_excel(_drive_service, file_id):
    request = _drive_service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_stream.seek(0)
    
    df = pd.read_excel(file_stream)
    # Blindaje contra espacios invisibles en las columnas
    df.columns = df.columns.str.strip() 
    return df

@st.cache_data(ttl=3600)
def cargar_bases():
    drive = get_drive_service()
    df_precios = descargar_excel(drive, st.secrets["ID_LISTA_PRECIOS"])
    df_correcciones = descargar_excel(drive, st.secrets["ID_CORRECCIONES"])
    df_marcas = descargar_excel(drive, st.secrets["ID_MARCAS"])
    return df_precios, df_correcciones, df_marcas

# --- Lógica de IA y Procesamiento ---
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
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            return json.loads(response.json()['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        return None
    return None

def procesar_cotizacion(texto_cliente, df_precios, df_correcciones, df_marcas):
    # 1. Bypass determinista
    bypass = df_correcciones[df_correcciones['Texto Cliente'].str.lower() == str(texto_cliente).lower().strip()]
    if not bypass.empty:
        return bypass.iloc[0]['Codigo Oficial JIELI']
    
    # 2. Extracción IA
    datos = llamar_llm_gemini(texto_cliente)
    if not datos:
        return "ERROR_LLM"
    
    producto = datos.get('producto', '')
    marca_solicitada = datos.get('marca', '')
    
    # 3. Fallback en texto sucio (Hasta que normalices la columna Marca)
    candidatos = df_precios[df_precios['Detalle'].str.contains(str(producto), case=False, na=False)]
    if marca_solicitada:
        candidatos = candidatos[candidatos['Detalle'].str.contains(str(marca_solicitada), case=False, na=False)]
    
    if candidatos.empty:
        return "SIN_COINCIDENCIAS"
    
    return candidatos.iloc[0]['Código']

# --- Interfaz Visual y Ejecución Masiva ---
try:
    df_precios, df_correcciones, df_marcas = cargar_bases()
    st.success("Bases de datos base sincronizadas.", icon="✅")
except Exception as e:
    st.error(f"Error de conexión con Drive. Revisá los Secrets y permisos: {e}")
    st.stop()

st.markdown("---")
st.subheader("Procesamiento Masivo de Cotizaciones")

archivo_cliente = st.file_uploader("Subí el Excel o CSV del cliente", type=["xlsx", "xls", "csv"])

if archivo_cliente:
    if archivo_cliente.name.endswith('.csv'):
        df_cliente = pd.read_csv(archivo_cliente)
    else:
        df_cliente = pd.read_excel(archivo_cliente)
    
    # Limpieza básica
    df_cliente = df_cliente.dropna(how='all')
    # Normalizar las columnas del archivo subido también por seguridad
    df_cliente.columns = df_cliente.columns.str.strip()
    
    st.write("Vista previa del archivo:")
    st.dataframe(df_cliente.head(3))
    
    # Selector de columna
    columnas_disponibles = df_cliente.columns.tolist()
    columna_texto = st.selectbox("Seleccioná qué columna contiene el detalle del material:", columnas_disponibles)
    
    if st.button("Procesar Cotización Completa", type="primary"):
        resultados = []
        barra_progreso = st.progress(0)
        total_filas = len(df_cliente)
        
        with st.spinner("Procesando matriz fila por fila..."):
            for index, row in df_cliente.iterrows():
                texto_item = str(row[columna_texto])
                
                # Ignorar celdas vacías
                if texto_item.strip() == "" or texto_item.lower() == "nan":
                    resultados.append("FILA_VACIA")
                else:
                    codigo = procesar_cotizacion(texto_item, df_precios, df_correcciones, df_marcas)
                    resultados.append(codigo)
                    # Delay táctico obligatorio para el rate limit de Gemini
                    time.sleep(2) 
                
                progreso_actual = (index + 1) / total_filas
                barra_progreso.progress(progreso_actual)
        
        df_cliente['SKU_Asignado'] = resultados
        
        st.success("Procesamiento finalizado.")
        st.dataframe(df_cliente)
        
        # Buffer en memoria para la descarga
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_cliente.to_excel(writer, index=False, sheet_name='Cotización Procesada')
        
        st.download_button(
            label="Descargar Excel Procesado",
            data=output.getvalue(),
            file_name="cotizacion_procesada.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
