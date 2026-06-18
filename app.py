import streamlit as st
import pandas as pd
import re
import unicodedata
import os

# --- INTENTAR IMPORTAR LA IA (Por si querés conectarla después) ---
# Podés usar OpenAI o Anthropic. Usaremos una estructura flexible.
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# Configuración de la página web
st.set_page_config(page_title="Cotizador Multimodal JIELI", page_icon="⚡", layout="wide")

st.title("⚡ Cotizador Inteligente Multimodal — JIELI")
st.markdown("Cargá tu lista de precios actualizada y procesá pedidos en **Excel, Texto o Imágenes** al instante.")

# --- FUNCIONES DE PROCESAMIENTO LOCAL ---
def normalizar_texto(texto):
    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()

def buscar_producto_local(detalle_cliente, df_precios):
    palabras = [w for w in re.split(r'\W+', normalizar_texto(detalle_cliente)) if len(w) > 2]
    if not palabras:
        return None
    # Intento 1: Coincidencia total de palabras
    mejores = df_precios[df_precios['_norm_detalle'].apply(lambda x: all(p in x for p in palabras))]
    if not mejores.empty:
        return mejores.iloc[0]
    # Intento 2: Coincidencia de la primera palabra clave
    flexibles = df_precios[df_precios['_norm_detalle'].str.contains(palabras[0], na=False)]
    if not flexibles.empty:
        return flexibles.iloc[0]
    return None

# --- PASO 1: CARGAR LA LISTA DE PRECIOS DEL NEGOCIO ---
st.header("1️⃣ Paso 1: Cargá la Lista de Precios de JIELI")
archivo_precios = st.file_uploader("Subí acá tu archivo oficial de precios (.xlsx)", type=["xlsx"], key="lista_maestra")

df_oficial = None

if archivo_precios is not None:
    try:
        # Asumimos que saltea las primeras 2 filas de títulos según la estructura original
        df_oficial = pd.read_excel(archivo_precios, skiprows=2)
        # Forzamos nombres de columnas estándar para que no falle el motor
        df_oficial.columns = ["codigo", "detalle", "moneda", "precio_siva", "precio_civa", "tasa_iva"] + list(df_oficial.columns[6:])
        df_oficial['_norm_detalle'] = df_oficial['detalle'].apply(normalizar_texto)
        st.success(f"¡Catálogo cargado con éxito! Se detectaron {len(df_oficial)} productos listos para cotizar.")
    except Exception as e:
        st.error(f"Error al procesar la lista de precios: {e}. Verificá que tenga las columnas requeridas.")

st.markdown("---")

# --- PASO 2: CARGAR EL PEDIDO DEL CLIENTE ---
st.header("2️⃣ Paso 2: Cargá el Pedido de Cotización")

if df_oficial is None:
    st.warning("⚠️ Primero debes cargar una lista de precios en el Paso 1 para habilitar este sector.")
else:
    # Pestañas para elegir el formato que envió el cliente
    tab_excel, tab_texto, tab_imagen = st.tabs(["📊 Archivo Excel / CSV", "✍️ Texto de WhatsApp", "📸 Imagen / Foto"])
    
    pedido_procesado = [] # Aquí guardaremos lo que extraigamos de cualquier método
    
    # --- TABLA EXCEL ---
    with tab_excel:
        archivo_cliente = st.file_uploader("Subí el presupuesto/pliego del cliente (.xlsx, .csv)", type=["xlsx", "csv"], key="cliente_excel")
        if archivo_cliente is not None:
            try:
                df_c = pd.read_csv(archivo_cliente) if archivo_cliente.name.endswith('.csv') else pd.read_excel(archivo_cliente)
                cols = df_c.columns.tolist()
                
                c1, c2 = st.columns(2)
                col_des = c1.selectbox("Columna de Descripción/Producto", cols, index=1 if len(cols)>1 else 0)
                col_cant = c2.selectbox("Columna de Cantidad", cols, index=2 if len(cols)>2 else 0)
                
                if st.button("Procesar Excel", key="btn_excel"):
                    for _, fila in df_c.iterrows():
                        desc = str(fila[col_des])
                        cant = fila[col_cant]
                        if desc.strip() == "" or "ETAPA" in desc.upper() or desc == "nan":
                            continue
                        pedido_procesado.append({"descripcion": desc, "cantidad": cant})
            except Exception as e:
                st.error(f"Error: {e}")

    # --- TEXTO LIBRE ---
    with tab_texto:
        texto_cliente = st.text_area("Pegá acá el texto o mensaje de WhatsApp que te mandó el cliente:", 
                                     placeholder="Ejemplo:\n900 metros cable unipolar 1.5 rojo\n3 tableros embutir 18 modulos\n6 termicas bipolares 10a")
        if st.button("Procesar Texto Libre", key="btn_texto"):
            # Lógica de extracción por líneas (Fácil ante la falta de API Key)
            lineas = texto_cliente.split("\n")
            for linea in lineas:
                if linea.strip():
                    # Intentamos buscar un número al inicio para asumir la cantidad
                    match_cant = re.match(r'^(\d+)', linea.strip())
                    if match_cant:
                        cant = float(match_cant.group(1))
                        desc = linea.replace(match_cant.group(1), "", 1).strip()
                    else:
                        cant = 1.0
                        desc = linea.strip()
                    pedido_procesado.append({"descripcion": desc, "cantidad": cant})

    # --- IMÁGENES / FOTOS ---
    with tab_imagen:
        foto_cliente = st.file_uploader("Subí la foto de la lista de materiales (.jpg, .png, .jpeg)", type=["jpg", "png", "jpeg"])
        if foto_cliente is not None:
            st.image(foto_cliente, caption="Foto del pedido", width=300)
            
            st.info("ℹ️ Para procesar imágenes mediante OCR inteligente y extraer los renglones de forma automática, se requiere configurar la API Key de OpenAI o Anthropic.")
            
            # Formulario simulado por si no está la IA activa todavía
            if not HAS_OPENAI:
                st.warning("El módulo de IA Vision no está configurado en este entorno local. Podés transcribir el texto rápidamente en la pestaña 'Texto de WhatsApp'.")

    # --- PROCESAMIENTO FINAL Y CRUCE CONTRA TU CATÁLOGO ---
    if pedido_procesado:
        st.markdown("### 💰 Resultado de la Cotización Automática")
        
        resultados_finales = []
        for item in pedido_procesado:
            desc_c = item["descripcion"]
            cant_c = item["cantidad"]
            
            # Validar cantidad numérica
            try:
                cant_c = float(cant_c) if pd.notna(cant_c) else 1.0
            except:
                cant_c = 1.0
                
            match = buscar_producto_local(desc_c, df_oficial)
            
            if match is not None:
                p_siva = float(match['precio_siva']) if pd.notna(match['precio_siva']) else 0.0
                p_civa = float(match['precio_civa']) if pd.notna(match['precio_civa']) else 0.0
                sub_siva = p_siva * cant_c
                sub_civa = p_civa * cant_c
                
                resultados_finales.append({
                    "Solicitado por Cliente": desc_c,
                    "Cant.": cant_c,
                    "Producto JIELI Sugerido": match['detalle'],
                    "Código": match['codigo'],
                    "Unit. S/IVA": f"${p_siva:,.2f}",
                    "Subtotal S/IVA": sub_siva,
                    "Subtotal C/IVA": sub_civa,
                    "Estado": "✅ Encontrado"
                })
            else:
                resultados_finales.append({
                    "Solicitado por Cliente": desc_c,
                    "Cant.": cant_c,
                    "Producto JIELI Sugerido": "⚠️ NO ENCONTRADO - Revisar Catálogo",
                    "Código": "—",
                    "Unit. S/IVA": "$0.00",
                    "Subtotal S/IVA": 0.0,
                    "Subtotal C/IVA": 0.0,
                    "Estado": "❌ Sin Match"
                })
        
        df_res = pd.DataFrame(resultados_finales)
        st.dataframe(df_res, use_container_width=True)
        
        tot_siva = df_res["Subtotal S/IVA"].sum()
        tot_civa = df_res["Subtotal C/IVA"].sum()
        
        col1, col2 = st.columns(2)
        col1.metric("TOTAL NETO (Sin IVA)", f"${tot_siva:,.2f}")
        col2.metric("TOTAL FACTURADO (Con IVA)", f"${tot_civa:,.2f}")
        
        # Generar botón de descarga Excel
        import io
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_res.to_excel(writer, index=False, sheet_name='Presupuesto')
        
        st.download_button(
            label="📥 Descargar Presupuesto Listo en Excel",
            data=output.getvalue(),
            file_name="presupuesto_jieli.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
