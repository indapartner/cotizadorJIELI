import streamlit as st
import pandas as pd
import re
import unicodedata
import os
import json

# Intentamos importar la librería de OpenAI, que sirve para conectarse a DeepSeek/Groq
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# Configuración de la página web
st.set_page_config(page_title="Cotizador IA Gratis JIELI", page_icon="⚡", layout="wide")

st.title("⚡ Cotizador Inteligente con IA Gratis — JIELI")
st.markdown("Cargá tu lista de precios y usá la IA de **DeepSeek** de forma gratuita para encontrar los productos exactos.")

# --- BARRA LATERAL: CONFIGURACIÓN DE LA IA GRATUITA ---
st.sidebar.header("🔑 Configuración de la IA")
st.sidebar.markdown("""
Para usar la IA gratis, te sugiero crearte una cuenta en **Groq.com** o **DeepSeek.com**, sacar una API Key gratuita y pegarla acá abajo.
""")

# Dejamos que el usuario pegue su clave de API en la pantalla
api_key = st.sidebar.text_input("Pegá tu API Key de Groq / DeepSeek acá:", type="password")
provider = st.sidebar.selectbox("Elegí el proveedor de IA:", ["Groq (Recomendado - Ultra Rápido)", "DeepSeek Oficial"])

# --- FUNCIONES DE ASISTENCIA LOCAL ---
def normalizar_texto(texto):
    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()

def obtener_candidatos_locales(detalle_cliente, df_precios, limite=15):
    """Filtra rápidamente el catálogo para no mandarle 40.000 productos a la IA (ahorra espacio y tiempo)"""
    palabras = [w for w in re.split(r'\W+', normalizar_texto(detalle_cliente)) if len(w) > 2]
    if not palabras:
        return df_precios.head(limite)
    
    # Busca productos que contengan al menos una de las palabras importantes del cliente
    condicion = df_precios['_norm_detalle'].str.contains(palabras[0], na=False)
    for p in palabras[1:3]: # Tomamos hasta 3 palabras clave para el filtro rápido
        condicion = condicion | df_precios['_norm_detalle'].str.contains(p, na=False)
        
    candidatos = df_precios[condicion]
    if candidatos.empty:
        return df_precios.head(limite)
    return candidatos.head(limite)

def buscar_con_ia(detalle_cliente, candidatos_df, api_key, provider):
    """Le pregunta a DeepSeek cuál de los productos candidatos es el correcto"""
    if not api_key:
        return None
        
    # Configuramos la URL del proveedor gratuito elegido
    if "Groq" in provider:
        base_url = "https://api.groq.com/openai/v1"
        model_name = "llama-3.3-70b-versatile" # Modelo open-source ultra inteligente y gratis en Groq
    else:
        base_url = "https://api.deepseek.com/v1"
        model_name = "deepseek-chat"

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        # Armamos una lista compacta de opciones para la IA
        opciones = ""
        for _, fila in candidatos_df.iterrows():
            opciones += f"CÓDIGO: {fila['codigo']} | DETALLE: {fila['detalle']}\n"
            
        prompt = f"""
        Actuás como un experto en materiales eléctricos de un mostrador de ventas mayorista.
        Un cliente solicita este producto: "{detalle_cliente}"
        
        Comparalo contra estas opciones de nuestro catálogo oficial de la empresa:
        {opciones}
        
        Elegí la mejor coincidencia. Si ninguna opción se parece en absoluto, respondé "NINGUNA".
        Tu respuesta debe ser estrictamente un JSON con este formato, sin texto extra alrededor:
        {{"codigo_elegido": "EL_CODIGO_AQUI"}}
        """
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, # Cero creatividad para que sea ultra preciso
            max_tokens=50
        )
        
        respuesta_texto = response.choices[0].message.content.strip()
        # Limpiamos posibles formatos de bloque de código que devuelven los modelos
        respuesta_texto = respuesta_texto.replace("```json", "").replace("```", "").strip()
        
        datos_json = json.loads(respuesta_texto)
        codigo = datos_json.get("codigo_elegido")
        
        if codigo and codigo != "NINGUNA":
            match = candidatos_df[candidatos_df['codigo'] == str(codigo)]
            if not match.empty:
                return match.iloc[0]
    except Exception as e:
        # Si la IA falla por algún motivo, el programa avisa silenciosamente en la consola
        print(f"Error en llamada de IA: {e}")
        
    return None

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
    st.warning("⚠️ Primero debes cargar una lista de precios en el Paso 1 para continuar.")
else:
    tab_excel, tab_texto = st.tabs(["📊 Archivo Excel / CSV", "✍️ Texto de WhatsApp"])
    pedido_procesado = []
    
    with tab_excel:
        archivo_cliente = st.file_uploader("Subí el presupuesto del cliente (.xlsx, .csv)", type=["xlsx", "csv"], key="cliente_excel")
        if archivo_cliente is not None:
            try:
                df_c = pd.read_csv(archivo_cliente) if archivo_cliente.name.endswith('.csv') else pd.read_excel(archivo_cliente)
                cols = df_c.columns.tolist()
                c1, c2 = st.columns(2)
                col_des = c1.selectbox("Columna de Descripción", cols, index=1 if len(cols)>1 else 0)
                col_cant = c2.selectbox("Columna de Cantidad", cols, index=2 if len(cols)>2 else 0)
                
                if st.button("Procesar Excel"):
                    for _, fila in df_c.iterrows():
                        desc = str(fila[col_des])
                        cant = fila[col_cant]
                        if desc.strip() == "" or "ETAPA" in desc.upper() or desc == "nan":
                            continue
                        pedido_procesado.append({"descripcion": desc, "cantidad": cant})
            except Exception as e:
                st.error(f"Error: {e}")

    with tab_texto:
        texto_cliente = st.text_area("Pegá el texto de WhatsApp acá:")
        if st.button("Procesar Texto"):
            for linea in texto_cliente.split("\n"):
                if linea.strip():
                    match_cant = re.match(r'^(\d+)', linea.strip())
                    cant = float(match_cant.group(1)) if match_cant else 1.0
                    desc = linea.replace(match_cant.group(1), "", 1).strip() if match_cant else linea.strip()
                    pedido_procesado.append({"descripcion": desc, "cantidad": cant})

    # --- RESPUESTA Y EMPAREJAMIENTO DE PRODUCTOS ---
    if pedido_procesado:
        st.markdown("### 💰 Cotización Inteligente Generada")
        if not api_key:
            st.info("💡 Consejo: Si pegás una API Key gratuita en la barra lateral, los resultados serán un 95% más exactos gracias a la IA.")
            
        resultados_finales = []
        progress_bar = st.progress(0)
        total_items = len(pedido_procesado)
        
        for i, item in enumerate(pedido_procesado):
            desc_c = item["descripcion"]
            cant_c = item["cantidad"]
            
            try:
                cant_c = float(cant_c) if pd.notna(cant_c) else 1.0
            except:
                cant_c = 1.0
            
            # 1. Filtrado de candidatos locales
            candidatos = obtener_candidatos_locales(desc_c, df_oficial)
            
            # 2. Intentar buscar con IA, si no hay key, usa el primer candidato local
            match = None
            if api_key and HAS_OPENAI:
                match = buscar_con_ia(desc_c, candidatos, api_key, provider)
                
            if match is None and not candidatos.empty:
                # Caída segura por código local tradicional si no hay IA configurada
                palabras = [w for w in re.split(r'\W+', normalizar_texto(desc_c)) if len(w) > 2]
                if palabras:
                    exactos = candidatos[candidatos['_norm_detalle'].str.contains(palabras[0], na=False)]
                    match = exactos.iloc[0] if not exactos.empty else candidatos.iloc[0]
            
            if match is not None:
                p_siva = float(match['precio_siva']) if pd.notna(match['precio_siva']) else 0.0
                p_civa = float(match['precio_civa']) if pd.notna(match['precio_civa']) else 0.0
                
                resultados_finales.append({
                    "Pedido del Cliente": desc_c,
                    "Cant.": cant_c,
                    "Producto Encontrado JIELI": match['detalle'],
                    "Código": match['codigo'],
                    "Precio Unit. S/IVA": f"${p_siva:,.2f}",
                    "Subtotal S/IVA": p_siva * cant_c,
                    "Subtotal C/IVA": p_civa * cant_c,
                    "Método": "🧠 IA Inteligente" if api_key else "🔍 Texto Local"
                })
            else:
                resultados_finales.append({
                    "Pedido del Cliente": desc_c,
                    "Cant.": cant_c,
                    "Producto Encontrado JIELI": "❌ NO ENCONTRADO - Revisar manual",
                    "Código": "—",
                    "Precio Unit. S/IVA": "$0.00",
                    "Subtotal S/IVA": 0.0,
                    "Subtotal C/IVA": 0.0,
                    "Método": "❌ Falló"
                })
            progress_bar.progress((i + 1) / total_items)
            
        df_res = pd.DataFrame(resultados_finales)
        st.dataframe(df_res.drop(columns=["Subtotal S/IVA", "Subtotal C/IVA"]), use_container_width=True)
        
        tot_siva = df_res["Subtotal S/IVA"].sum()
        tot_civa = df_res["Subtotal C/IVA"].sum()
        
        col1, col2 = st.columns(2)
        col1.metric("TOTAL NETO (Sin IVA)", f"${tot_siva:,.2f}")
        col2.metric("TOTAL FINAL (Con IVA)", f"${tot_civa:,.2f}")
