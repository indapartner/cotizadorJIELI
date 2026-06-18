import streamlit as st
import pandas as pd
import re
import unicodedata
import os
import json

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

st.set_page_config(page_title="Cotizador JIELI con Validación", page_icon="⚡", layout="wide")

st.title("⚡ Cotizador Inteligente con Control de Calidad — JIELI")
st.markdown("Si el sistema no está 100% seguro de un producto, te va a pedir que lo selecciones manualmente antes de darte los precios finales.")

# --- BARRA LATERAL: CONFIGURACIÓN DE LA IA (OPCIONAL) ---
st.sidebar.header("🔑 Configuración")
api_key = st.sidebar.text_input("Pegá tu API Key (Opcional si querés IA):", type="password")
provider = st.sidebar.selectbox("Proveedor de IA:", ["Groq (Llama 3.3)", "DeepSeek Oficial"])

# --- FUNCIONES DE NORMALIZACIÓN Y BÚSQUEDA ---
def normalizar_texto(texto):
    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()

def buscar_candidatos_estrictos(detalle_cliente, df_precios):
    """Busca productos locales usando el método viejo confiable de palabras clave"""
    palabras = [w for w in re.split(r'\W+', normalizar_texto(detalle_cliente)) if len(w) > 2]
    if not palabras:
        return pd.DataFrame()
    
    # Intento 1: Que contenga TODAS las palabras (Match de alta confianza)
    match_estricto = df_precios[df_precios['_norm_detalle'].apply(lambda x: all(p in x for p in palabras))]
    if not match_estricto.empty:
        return match_estricto
        
    # Intento 2: Que contenga al menos las dos primeras (Match intermedio)
    if len(palabras) >= 2:
        match_medio = df_precios[df_precios['_norm_detalle'].str.contains(palabras[0], na=False) & 
                                 df_precios['_norm_detalle'].str.contains(palabras[1], na=False)]
        if not match_medio.empty:
            return match_medio

    # Intento 3: Caída libre a la primera palabra importante (Baja confianza)
    return df_precios[df_precios['_norm_detalle'].str.contains(palabras[0], na=False)].head(10)

# --- PASO 1: CARGAR LA LISTA DE PRECIOS DEL NEGOCIO ---
st.header("1️⃣ Paso 1: Cargá la Lista de Precios de JIELI")
archivo_precios = st.file_uploader("Subí tu archivo oficial de precios (.xlsx)", type=["xlsx"], key="lista_maestra")

df_oficial = None

if archivo_precios is not None:
    try:
        df_oficial = pd.read_excel(archivo_precios, skiprows=2)
        df_oficial.columns = ["codigo", "detalle", "moneda", "precio_siva", "precio_civa", "tasa_iva"] + list(df_oficial.columns[6:])
        df_oficial['codigo'] = df_oficial['codigo'].astype(str).str.strip()
        df_oficial['_norm_detalle'] = df_oficial['detalle'].apply(normalizar_texto)
        st.success(f"¡Catálogo cargado! {len(df_oficial)} productos listos.")
    except Exception as e:
        st.error(f"Error al procesar la lista de precios: {e}")

st.markdown("---")

# --- PASO 2: CARGAR EL PEDIDO DEL CLIENTE ---
st.header("2️⃣ Paso 2: Cargá el Pedido de Cotización")

if df_oficial is None:
    st.warning("⚠️ Primero debes cargar una lista de precios en el Paso 1.")
else:
    tab_excel, tab_texto = st.tabs(["📊 Archivo Excel / CSV", "✍️ Texto de WhatsApp"])
    pedido_raw = []
    
    with tab_excel:
        archivo_cliente = st.file_uploader("Subí el presupuesto del cliente (.xlsx, .csv)", type=["xlsx", "csv"])
        if archivo_cliente is not None:
            try:
                df_c = pd.read_csv(archivo_cliente) if archivo_cliente.name.endswith('.csv') else pd.read_excel(archivo_cliente)
                cols = df_c.columns.tolist()
                c1, c2 = st.columns(2)
                col_des = c1.selectbox("Columna de Descripción", cols, index=1 if len(cols)>1 else 0)
                col_cant = c2.selectbox("Columna de Cantidad", cols, index=2 if len(cols)>2 else 0)
                
                for _, fila in df_c.iterrows():
                    desc = str(fila[col_des])
                    cant = fila[col_cant]
                    if desc.strip() == "" or "ETAPA" in desc.upper() or desc == "nan":
                        continue
                    pedido_raw.append({"descripcion": desc, "cantidad": cant})
            except Exception as e:
                st.error(f"Error: {e}")

    with tab_texto:
        texto_cliente = st.text_area("Pegá el texto de WhatsApp acá:")
        if texto_cliente:
            for linea in texto_cliente.split("\n"):
                if linea.strip():
                    match_cant = re.match(r'^(\d+)', linea.strip())
                    cant = float(match_cant.group(1)) if match_cant else 1.0
                    desc = linea.replace(match_cant.group(1), "", 1).strip() if match_cant else linea.strip()
                    pedido_raw.append({"descripcion": desc, "cantidad": cant})

    # --- NUEVA LÓGICA: PANTALLA INTERMEDIA DE REVISIÓN ---
    if pedido_raw:
        st.markdown("---")
        st.header("3️⃣ Paso 3: Control de Calidad (Validación del Vendedor)")
        st.info("Revisá los productos que tienen dudas. Si el sistema encontró un único match perfecto, ya lo pre-seleccionó.")
        
        # Usamos un formulario para congelar la pantalla hasta que el vendedor termine de elegir
        with st.form("formulario_validacion"):
            respuestas_usuario = {}
            
            for i, item in enumerate(pedido_raw):
                desc_c = item["descripcion"]
                cant_c = item["cantidad"]
                
                # Buscamos candidatos reales en tu Excel
                candidatos = buscar_candidatos_estrictos(desc_c, df_oficial)
                
                st.markdown(f"**Item del Cliente:** `{cant_c}` x *\"{desc_c}\"*")
                
                if candidatos.empty:
                    st.warning("❌ No se encontró ninguna coincidencia en el catálogo.")
                    respuestas_usuario[i] = "NO_ENCONTRADO"
                elif len(candidatos) == 1:
                    # SI HAY UN SOLO RESULTADO PERFECTO: Se selecciona directo (Match Automático)
                    fila = candidatos.iloc[0]
                    st.success(f"✅ Match Automático Directo: **{fila['detalle']}** (Cód: {fila['codigo']})")
                    respuestas_usuario[i] = fila['codigo']
                else:
                    # SI HAY DUDAS (Múltiples opciones): Creamos un desplegable interactivo para el vendedor
                    opciones_combo = []
                    mapeo_codigos = {}
                    
                    for _, fila in candidatos.iterrows():
                        label = f"Cód: {fila['codigo']} | {fila['detalle']} | Precio S/IVA: ${fila['precio_siva']}"
                        opciones_combo.append(label)
                        mapeo_codigos[label] = fila['codigo']
                    
                    opciones_combo.append("❌ Ninguno de estos es correcto (Saltar ítem)")
                    mapeo_codigos["❌ Ninguno de estos es correcto (Saltar ítem)"] = "SALTAR"
                    
                    # El vendedor elige en vivo cuál es el verdadero producto
                    seleccionado = st.selectbox(
                        "⚠️ Encontré varias opciones parecidas. Sele
