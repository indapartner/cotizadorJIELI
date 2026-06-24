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
def descargar_archivo(_drive_service, file_id, header=0):
    request = _drive_service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_stream.seek(0)
    
    try:
        # Intentamos leer como Excel primero
        df = pd.read_excel(file_stream, header=header)
    except ValueError:
        # Si tira error, probamos como CSV (muy común en descargas de Drive)
        file_stream.seek(0)
        df = pd.read_csv(file_stream, header=header)
        
    # Blindaje contra espacios invisibles en los nombres de las columnas
    df.columns = df.columns.str.strip() 
    return df

@st.cache_data(ttl=3600)
def cargar_bases():
    drive = get_drive_service()
    
    # Lista de precios tiene una fila en blanco arriba de los títulos (header=1)
    df_precios = descargar_archivo(drive, st.secrets["ID_LISTA_PRECIOS"], header=1)
    
    # Correcciones y marcas inician en la primera fila (header=0)
    df_correcciones = descargar_archivo(drive, st.secrets["ID_CORRECCIONES"], header=0)
    df_marcas = descargar_archivo(drive, st.secrets["ID_MARCAS"], header=0)
    
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
    # 1. Bypass determinista (Verificando que las columnas existan para evitar KeyErrors)
    col_texto = 'Texto Cliente'
    col_codigo_correccion = 'Codigo Oficial JIELI'
    
    if col_texto in df_correcciones.columns and col_codigo_correccion in df_correcciones.columns:
        bypass = df_correcciones[df_correcciones[col_texto].str.lower() == str(texto_cliente).lower().strip()]
        if not bypass.empty:
            return bypass.iloc[0][col_codigo_correccion]
    else:
        return "ERROR_COLUMNAS_CORRECCIONES"
    
    # 2. Extracción IA
    datos = llamar_llm_gemini(texto_cliente)
    if not datos:
        return "ERROR_LLM"
    
    producto = datos.get('producto', '')
    marca_solicitada = datos.get('marca', '')
    
    # 3. Fallback en texto sucio
    col_detalle = 'Detalle'
    col_codigo = 'Código'
    
    if col_detalle in df_precios.columns and col_codigo in df_precios.columns:
        candidatos = df_precios[df_precios[col_detalle].str.contains(str(producto), case=False, na=False)]
        
        # Validar la marca solo si no es nula/vacía
        if marca_solicitada and str(marca_solicitada).lower() != "null":
            candidatos = candidatos[candidatos[col_detalle].str.contains(str(marca_solicitada), case=False, na=False)]
        
        if candidatos.empty:
            return "SIN_COINCIDENCIAS"
        
        return candidatos.iloc[0][col_codigo]
    else:
        return "ERROR_COLUMNAS_PRECIOS"

# --- Interfaz Visual y Ejecución Masiva ---
try:
    df_precios, df_correcciones, df_marcas = cargar_bases()
    st.success("Bases de datos base sincronizadas.", icon="✅")
except Exception as e:
    st.error(f"Error de conexión con Drive o lectura de archivos. Detalles: {e}")
    st.stop()

st.markdown("---")
st.subheader("Procesamiento Masivo de Cotizaciones")

archivo_cliente = st.file_uploader("Subí el Excel o CSV del cliente", type=["xlsx", "xls", "csv"])

if archivo_cliente:
    try:
        if archivo_cliente.name.endswith('.csv'):
            df_cliente = pd.read_csv(archivo_cliente)
        else:
            df_cliente = pd.read_excel(archivo_cliente)
            
        # Limpieza básica
        df_cliente = df_cliente.dropna(how='all')
        df_cliente.columns = df_cliente.columns.str.strip()
        
        st.write("Vista previa del archivo:")
        st.dataframe(df_cliente.head(3))
        
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
    except Exception as e:
        st.error(f"Error al leer el archivo del cliente: {e}")
