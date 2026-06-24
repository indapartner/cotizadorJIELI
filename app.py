import streamlit as st
import pandas as pd
import json
import requests
import io
import time
import re
import concurrent.futures
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="Cotizador B2B", layout="centered", page_icon="⚡")
st.title("Cotizador IA - B2B")

# --- Función de Normalización Básica ---
def normalizar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower().strip()
    t = t.replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u')
    t = t.replace(',', '.')
    t = t.replace('.00', '').replace('.0', '')
    t = re.sub(r'\s*x\s*', 'x', t) 
    t = re.sub(r'(\d)\s+(mm2|mm|a|v|w|kva|hp|cv|m|cm|kv)\b', r'\1\2', t) 
    t = t.replace('"', '').replace("'", "")
    return " ".join(t.split())

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
    return df_precios, df_correcciones

# --- LA NUEVA LÓGICA: IA COMO JUEZ FINAL ---
def llamar_llm_juez(texto_cliente, lista_candidatos):
    api_key = st.secrets["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    prompt = f"""
    Eres el jefe de ventas de una casa de materiales eléctricos.
    Un cliente te pidió cotizar exactamente esto: "{texto_cliente}"
    
    Tu asistente buscó en el sistema y te trajo una pre-selección con estos posibles productos de la lista oficial:
    {json.dumps(lista_candidatos, indent=2, ensure_ascii=False)}
    
    Tu tarea:
    1. Analizar semánticamente el pedido del cliente (ej: "térmica" = "termomagnética", "4mm" = "4.00 mm", "cable" = "conductor").
    2. Leer la pre-selección y elegir el producto que cumple EXACTAMENTE con lo pedido.
    3. Si ninguna de las opciones sirve, debes rechazarlo.
    
    Devuelve ÚNICAMENTE un JSON con esta estructura:
    {{"codigo_elegido": "el código alfanumérico del producto ganador, o la palabra 'SIN_COINCIDENCIAS'", "razonamiento": "por qué lo elegiste en 10 palabras"}}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}
    }
    
    try:
        # Le ponemos un timeout corto por si Google se cuelga, así no frena el resto
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            return {"error": f"Error API {response.status_code}"}
            
        data = response.json()
        if 'candidates' not in data or len(data['candidates']) == 0:
            return {"error": "Respuesta vacía"}
            
        texto_respuesta = data['candidates'][0]['content']['parts'][0]['text']
        texto_respuesta = texto_respuesta.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(texto_respuesta)
        
    except Exception as e:
        return {"error": str(e)}

def procesar_cotizacion(texto_cliente, df_precios, df_correcciones):
    try:
        cols_corr = [str(c).lower().strip() for c in df_correcciones.columns]
        if 'texto cliente' in cols_corr and 'codigo oficial jieli' in cols_corr:
            df_corr_temp = df_correcciones.copy()
            df_corr_temp.columns = cols_corr
            bypass = df_corr_temp[df_corr_temp['texto cliente'].astype(str).str.lower() == str(texto_cliente).lower().strip()]
            if not bypass.empty:
                codigo_ganador = bypass.iloc[0]['codigo oficial jieli']
                return obtener_datos_por_codigo(codigo_ganador, df_precios)
    except Exception:
        pass

    col_codigo = next((c for c in df_precios.columns if 'código' in str(c).lower() or 'codigo' in str(c).lower()), None)
    col_detalle = next((c for c in df_precios.columns if 'detalle' in str(c).lower()), None)
    
    if not col_codigo or not col_detalle:
        return "ERROR_COLUMNAS_PRECIOS", "-", 0.0

    df_temp = df_precios[[col_codigo, col_detalle]].dropna(subset=[col_detalle]).copy()
    df_temp['detalle_norm'] = df_temp[col_detalle].apply(normalizar_texto)
    texto_norm = normalizar_texto(texto_cliente)
    
    palabras = [p for p in texto_norm.split() if len(p) > 1]
    df_temp['score'] = 0
    for palabra in palabras:
        df_temp['score'] += df_temp['detalle_norm'].str.contains(re.escape(palabra), case=False, regex=True).astype(int)
    
    candidatos_top = df_temp[df_temp['score'] > 0].sort_values(by='score', ascending=False).head(20)
    
    if candidatos_top.empty:
        return "SIN_COINCIDENCIAS", "NO_ENCONTRADO", 0.0

    lista_para_ia = candidatos_top[[col_codigo, col_detalle]].rename(columns={col_codigo: "codigo", col_detalle: "detalle"}).to_dict(orient='records')
    respuesta_ia = llamar_llm_juez(texto_cliente, lista_para_ia)
    
    if isinstance(respuesta_ia, dict) and "error" in respuesta_ia:
        return f"ERROR_LLM: {respuesta_ia['error']}", "-", 0.0
        
    codigo_ganador = respuesta_ia.get('codigo_elegido', 'SIN_COINCIDENCIAS')
    if codigo_ganador == 'SIN_COINCIDENCIAS':
        return "SIN_COINCIDENCIAS", "NO_ENCONTRADO", 0.0
        
    return obtener_datos_por_codigo(codigo_ganador, df_precios)

def obtener_datos_por_codigo(codigo, df_precios):
    cols_precios = [str(c).lower().strip() for c in df_precios.columns]
    df_temp = df_precios.copy()
    df_temp.columns = cols_precios
    
    col_codigo = next((c for c in cols_precios if 'código' in c or 'codigo' in c), None)
    col_detalle = next((c for c in cols_precios if 'detalle' in c), None)
    col_precio = next((c for c in cols_precios if 'precio' in c), None)
    
    match = df_temp[df_temp[col_codigo].astype(str) == str(codigo)]
    
    if not match.empty:
        detalle = match.iloc[0][col_detalle] if col_detalle else "Sin Detalle"
        precio = 0.0
        if col_precio:
            val_precio = match.iloc[0][col_precio]
            if isinstance(val_precio, (int, float)):
                precio = float(val_precio)
            else:
                val_str = str(val_precio).replace('$', '').replace('.', '').replace(',', '.').strip()
                try: precio = float(val_str)
                except: pass
        return codigo, detalle, precio
    else:
        return codigo, "CÓDIGO_NO_EN_LISTA", 0.0

# --- Worker para el Paralelismo ---
def procesar_fila_worker(args):
    i, texto_item, cant_raw, calc_cant, df_p, df_c = args
    if texto_item.strip() == "" or texto_item.lower() == "nan":
        return i, "FILA_VACIA", "-", 0.0, 0.0
        
    codigo, detalle, precio = procesar_cotizacion(texto_item, df_p, df_c)
    
    subtotal = "-"
    if calc_cant:
        try:
            cantidad = float(str(cant_raw).replace(',', '.').strip())
            if isinstance(precio, (int, float)):
                subtotal = cantidad * precio
            else:
                subtotal = 0.0
        except:
            subtotal = "CANT_INVALIDA"
            
    return i, codigo, detalle, precio, subtotal

# --- Interfaz Visual ---
try:
    df_precios, df_correcciones = cargar_bases()
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
            total_filas = len(df_cliente)
            
            # Pre-armamos las listas vacías para mantener el orden original
            resultados_sku = [""] * total_filas
            resultados_detalle = [""] * total_filas
            resultados_precio = [0.0] * total_filas
            resultados_subtotal = [0.0] * total_filas
            
            # Preparamos las tareas para los "empleados"
            calc_cant = (columna_cantidad != "No calcular cantidad")
            tareas = []
            for i, (index, row) in enumerate(df_cliente.iterrows()):
                texto = str(row[columna_texto])
                cant = row[columna_cantidad] if calc_cant else None
                tareas.append((i, texto, cant, calc_cant, df_precios, df_correcciones))
            
            barra_progreso = st.progress(0.0)
            
            with st.spinner("La IA está analizando en MODO TURBO (Procesando filas en paralelo)..."):
                completados = 0
                
                # Desatamos 15 hilos en simultáneo a pegarle a la API
                with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                    futuros = [executor.submit(procesar_fila_worker, t) for t in tareas]
                    
                    for futuro in concurrent.futures.as_completed(futuros):
                        i, cod, det, pre, sub = futuro.result()
                        resultados_sku[i] = cod
                        resultados_detalle[i] = det
                        resultados_precio[i] = pre
                        resultados_subtotal[i] = sub
                        
                        completados += 1
                        barra_progreso.progress(min(completados / total_filas, 1.0))
            
            # Asignamos las columnas resultantes
            df_cliente['SKU_Asignado'] = resultados_sku
            df_cliente['Detalle_Lista_Oficial'] = resultados_detalle
            df_cliente['Precio_Unitario'] = resultados_precio
            if calc_cant:
                df_cliente['Subtotal_Calculado'] = resultados_subtotal
            
            st.success("Procesamiento finalizado a velocidad máxima.")
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
