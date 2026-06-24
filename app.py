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
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    # NUEVO PROMPT: Obligamos a la IA a separar TODO en atributos para no romper la búsqueda
    prompt = f"""
    Eres un experto en materiales eléctricos. Analiza esta solicitud: '{texto_cliente}'
    Extrae la información y devuelve ÚNICAMENTE un JSON con esta estructura:
    {{"producto": "palabra clave principal", "atributos": ["atr1", "atr2"], "marca": "marca si existe o null"}}
    REGLA: El "producto" debe ser de 1 o 2 palabras (ej: cable, termica, cano). Todo lo demás (medidas, polos, colores) va a "atributos".
    Ejemplo si es 'Termica 2x16 Sica': {{"producto": "termica", "atributos": ["2x16", "16A", "bipolar"], "marca": "Sica"}}
    Ejemplo si es 'cable unipolar 4mm verde': {{"producto": "cable", "atributos": ["unipolar", "4mm", "verde"], "marca": "null"}}
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

    # 1. Bypass determinista
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
    
    # 2. Extracción IA y SISTEMA DE PUNTAJE
    if not codigo_final:
        datos = llamar_llm_gemini(texto_cliente)
        
        if isinstance(datos, dict) and "error" in datos:
            error_msg = datos["error"]
        elif not datos:
            error_msg = "ERROR_LLM_DESCONOCIDO"
        else:
            producto = datos.get('producto', '')
            marca_solicitada = datos.get('marca', '')
            atributos = datos.get('atributos', [])
            
            try:
                df_precios_temp = df_precios.copy()
                df_precios_temp.columns = [str(c).lower().strip() for c in df_precios.columns]
                
                # Filtro inicial: Buscamos solo la palabra raíz (ej: "cable")
                prod_limpio = str(producto).lower().strip()
                candidatos = df_precios_temp[df_precios_temp['detalle'].astype(str).str.contains(prod_limpio, case=False, na=False)].copy()
                
                if not candidatos.empty:
                    # --- INICIO SISTEMA DE PUNTAJE ---
                    candidatos['score'] = 0
                    # Truco de magia: borramos los espacios de la lista de precios para que "4 mm" y "4mm" matcheen perfecto
                    detalle_sin_espacios = candidatos['detalle'].astype(str).str.lower().str.replace(" ", "")
                    
                    # Chequeo de marca (Peso fuerte: suma 2 puntos)
                    if marca_solicitada and str(marca_solicitada).lower() not in ["null", "none", ""]:
                        marca_limpia = str(marca_solicitada).lower().replace(" ", "")
                        candidatos['score'] += detalle_sin_espacios.str.contains(marca_limpia).astype(int) * 2
                    
                    # Chequeo de atributos (Suma 1 punto por cada coincidencia exacta sin espacios)
                    if isinstance(atributos, list):
                        for attr in atributos:
                            if str(attr).strip():
                                attr_limpio = str(attr).lower().replace(" ", "")
                                candidatos['score'] += detalle_sin_espacios.str.contains(attr_limpio).astype(int)
                    
                    # Ordenamos por los que sacaron mejor nota
                    candidatos = candidatos.sort_values(by='score', ascending=False)
                    
                    # Nos quedamos con el mejor (si hay empate en puntaje, queda el que Pandas filtró primero, que suele ser el más exacto)
                    codigo_final = candidatos.iloc[0]['código']
                    # -----------------------------------
                else:
                    error_msg = "SIN_COINCIDENCIAS"
            except Exception:
                error_msg = "ERROR_COLUMNAS_PRECIOS"

    # 3. Traer Detalle y Precio
    if codigo_final and not error_msg:
        try:
            df_precios_temp = df_precios.copy()
            cols_precios = [str(c).lower().strip() for c in df_precios.columns]
            df_precios_temp.columns = cols_precios
            
            if 'código' in cols_precios:
                match = df_precios_temp[df_precios_temp['código'].astype(str) == str(codigo_final)]
                if not match.empty:
                    detalle_final = match.iloc[0]['detalle'] if 'detalle' in cols_precios else "Sin columna 'Detalle'"
                    
                    col_precio = next((c for c in cols_precios if 'precio' in c), None)
                    if col_precio:
                        val_precio = match.iloc[0][col_precio]
                        try:
                            precio_final = float(str(val_precio).replace('$', '').replace('.', '').replace(',', '.').strip())
                        except:
                            precio_final = val_precio
                    else:
                        precio_final = 0.0
                else:
                    detalle_final = "CÓDIGO_NO_EN_LISTA"
                    precio_final = 0.0
        except Exception:
            detalle_final = "ERROR_BUSQUEDA_DATOS"
            precio_final = 0.0

    if error_msg:
        return error_msg, "-", 0.0
        
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
        
        col1, col2 = st.columns(2)
        with col1:
            columna_texto = st.selectbox("Seleccioná la columna del DETALLE del material:", columnas_disponibles)
        with col2:
            opciones_cantidad = ["No calcular cantidad"] + columnas_disponibles
            columna_cantidad = st.selectbox("Seleccioná la columna de la CANTIDAD:", opciones_cantidad)
        
        if st.button("Procesar Cotización Completa", type="primary"):
            resultados_sku = []
            resultados_detalle = []
            resultados_precio = []
            resultados_subtotal = []
            
            barra_progreso = st.progress(0.0)
            total_filas = len(df_cliente)
            
            with st.spinner("Procesando matriz fila por fila..."):
                for i, (index, row) in enumerate(df_cliente.iterrows()):
                    texto_item = str(row[columna_texto])
                    
                    if texto_item.strip() == "" or texto_item.lower() == "nan":
                        resultados_sku.append("FILA_VACIA")
                        resultados_detalle.append("-")
                        resultados_precio.append(0.0)
                        resultados_subtotal.append(0.0)
                    else:
                        codigo, detalle, precio = procesar_cotizacion(texto_item, df_precios, df_correcciones, df_marcas)
                        resultados_sku.append(codigo)
                        resultados_detalle.append(detalle)
                        resultados_precio.append(precio)
                        
                        if columna_cantidad != "No calcular cantidad":
                            try:
                                cant_raw = row[columna_cantidad]
                                cantidad = float(str(cant_raw).replace(',', '.').strip())
                                if isinstance(precio, (int, float)):
                                    resultados_subtotal.append(cantidad * precio)
                                else:
                                    resultados_subtotal.append(0.0)
                            except:
                                resultados_subtotal.append("CANT_INVALIDA")
                        else:
                            resultados_subtotal.append("-")
                            
                        time.sleep(0.1) 
                    
                    progreso_actual = min((i + 1) / total_filas, 1.0)
                    barra_progreso.progress(progreso_actual)
            
            df_cliente['SKU_Asignado'] = resultados_sku
            df_cliente['Detalle_Lista_Oficial'] = resultados_detalle
            df_cliente['Precio_Unitario'] = resultados_precio
            
            if columna_cantidad != "No calcular cantidad":
                df_cliente['Subtotal_Calculado'] = resultados_subtotal
            
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
        st.error(f"Error al procesar el archivo: {e}")
