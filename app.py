import streamlit as st
import pandas as pd
import json
import requests
import io
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="Cotizador B2B", layout="centered", page_icon="⚡")
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
        df = pd.read_excel(file_stream, header=header)
    except ValueError:
        file_stream.seek(0)
        df = pd.read_csv(file_stream, header=header)
        
    df.columns = df.columns.str.strip() 
    return df

@st.cache_data(ttl=3600)
def cargar_bases():
    drive = get_drive_service()
    
    df_precios = descargar_archivo(drive, st.secrets["ID_LISTA_PRECIOS"], header=1)
    df_correcciones = descargar_archivo(drive, st.secrets["ID_CORRECCIONES"], header=0)
    df_marcas = descargar_archivo(drive, st.secrets["ID_MARCAS"], header=0)
    
    return df_precios, df_correcciones, df_marcas

# --- Lógica de IA y Procesamiento ---
def llamar_llm_gemini(texto_cliente):
    api_key = st.secrets["GEMINI_API_KEY"]
    
    # Usamos la generación actual
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = f"""
    Eres un extractor de datos técnicos de materiales eléctricos.
    Analiza esta solicitud: '{texto_cliente}'
    Devuelve ÚNICAMENTE un JSON con esta estructura y nada más:
    {{"producto": "nombre generico", "atributos": ["atr1", "atr2"], "marca": "marca si existe o null"}}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    try:
        response = requests.post(url, json=payload)
        
        if response.status_code != 200:
            return {"error": f"Error API {response.status_code}: {response.text[:100]}"}
            
        data = response.json()
        
        if 'candidates' not in data or len(data['candidates']) == 0:
            return {"error": "Respuesta vacía o bloqueada por Gemini"}
            
        texto_respuesta = data['candidates'][0]['content']['parts'][0]['text']
        texto_respuesta = texto_respuesta.strip().removeprefix("```json").removesuffix("```").strip()
        
        return json.loads(texto_respuesta)
        
    except json.JSONDecodeError:
        return {"error": f"La IA no devolvió un JSON válido. Devolvió: {texto_respuesta[:50]}"}
    except Exception as e:
        return {"error": f"Falla interna: {str(e)}"}

def procesar_cotizacion(texto_cliente, df_precios, df_correcciones, df_marcas):
    codigo_final = None
    detalle_final = "NO_ENCONTRADO"
    precio_final = 0.0
    error_msg = None

    # 1. Bypass determinista (Opcional - Tolerante a fallos)
    try:
        cols_corr = [str(c).lower().strip() for c in df_correcciones.columns]
        if 'texto cliente' in cols_corr and 'codigo oficial jieli' in cols_corr:
            df_corr_temp = df_correcciones.copy()
            df_corr_temp.columns = cols_corr
            
            bypass = df_corr_temp[df_corr_temp['texto cliente'].astype(str).str.lower() == str(texto_cliente).lower().strip()]
            if not bypass.empty:
                codigo_final = bypass.iloc[0]['codigo oficial jieli']
    except Exception:
        pass
    
    # 2. Extracción IA y Búsqueda de Código (Si no hubo bypass)
    if not codigo_final:
        datos = llamar_llm_gemini(texto_cliente)
        
        if isinstance(datos, dict) and "error" in datos:
            error_msg = datos["error"]
        elif not datos:
            error_msg = "ERROR_LLM_DESCONOCIDO"
        else:
            producto = datos.get('producto', '')
            marca_solicitada = datos.get('marca', '')
            
            try:
                df_precios_temp = df_precios.copy()
                df_precios_temp.columns = [str(c).lower().strip() for c in df_precios.columns]
                
                candidatos = df_precios_temp[df_precios_temp['detalle'].astype(str).str.contains(str(producto), case=False, na=False)]
                
                if marca_solicitada and str(marca_solicitada).lower() != "null":
                    candidatos = candidatos[candidatos['detalle'].astype(str).str.contains(str(marca_solicitada), case=False, na=False)]
                
                if not candidatos.empty:
                    codigo_final = candidatos.iloc[0]['código']
                else:
                    error_msg = "SIN_COINCIDENCIAS"
            except Exception:
                error_msg = "ERROR_COLUMNAS_PRECIOS"

    # 3. Traer Detalle y Precio desde la base usando el Código Final
    if codigo_final and not error_msg:
        try:
            df_precios_temp = df_precios.copy()
            cols_precios = [str(c).lower().strip() for c in df_precios.columns]
            df_precios_temp.columns = cols_precios
            
            if 'código' in cols_precios:
                match = df_precios_temp[df_precios_temp['código'].astype(str) == str(codigo_final)]
                if not match.empty:
                    detalle_final = match.iloc[0]['detalle'] if 'detalle' in cols_precios else "Sin columna 'Detalle'"
                    
                    # Busca dinámicamente cualquier columna que contenga "precio"
                    col_precio = next((c for c in cols_precios if 'precio' in c), None)
                    if col_precio:
                        precio_final = match.iloc[0][col_precio]
                    else:
                        precio_final = "Sin columna 'Precio'"
                else:
                    detalle_final = "CÓDIGO_NO_EN_LISTA"
                    precio_final = "-"
        except Exception:
            detalle_final = "ERROR_BUSQUEDA_DATOS"
            precio_final = "ERROR"

    if error_msg:
        return error_msg, "-", "-"
        
    return codigo_final, detalle_final, precio_final

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
            
        df_cliente = df_cliente.dropna(how='all')
        df_cliente.columns = df_cliente.columns.str.strip()
        
        st.write("Vista previa del archivo:")
        st.dataframe(df_cliente.head(3))
        
        columnas_disponibles = df_cliente.columns.tolist()
        columna_texto = st.selectbox("Seleccioná qué columna contiene el detalle del material:", columnas_disponibles)
        
        if st.button("Procesar Cotización Completa", type="primary"):
            resultados_sku = []
            resultados_detalle = []
            resultados_precio = []
            
            barra_progreso = st.progress(0)
            total_filas = len(df_cliente)
            
            with st.spinner("Procesando matriz fila por fila..."):
                for index, row in df_cliente.iterrows():
                    texto_item = str(row[columna_texto])
                    
                    if texto_item.strip() == "" or texto_item.lower() == "nan":
                        resultados_sku.append("FILA_VACIA")
                        resultados_detalle.append("-")
                        resultados_precio.append("-")
                    else:
                        codigo, detalle, precio = procesar_cotizacion(texto_item, df_precios, df_correcciones, df_marcas)
                        resultados_sku.append(codigo)
                        resultados_detalle.append(detalle)
                        resultados_precio.append(precio)
                        
                        # --- PAUSA DE 5 SEGUNDOS (Límite 12 RPM para Google Free) ---
                        time.sleep(5) 
                    
                    progreso_actual = (index + 1) / total_filas
                    barra_progreso.progress(progreso_actual)
            
            # Agregamos las tres columnas al Excel final
            df_cliente['SKU_Asignado'] = resultados_sku
            df_cliente['Detalle_Lista_Oficial'] = resultados_detalle
            df_cliente['Precio_Unitario'] = resultados_precio
            
            st.success("Procesamiento finalizado.")
            st.dataframe(df_cliente)
            
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
