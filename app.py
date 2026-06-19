import streamlit as st
import pandas as pd
import re
import unicodedata
import io

# Configuración de la interfaz
st.set_page_config(page_title="Cotizador JIELI Automatizado", page_icon="⚡", layout="wide")

st.title("⚡ Cotizador Inteligente Conectado a Drive — JIELI")
st.markdown("Los catálogos, correctores de errores y prioridades se leen en vivo desde Google Drive. Cargá el pedido del cliente para cotizar.")

# --- DEFINICIÓN DE LINKS DE DESCARGA DIRECTA (TUS ENLACES REALES) ---
ID_PRECIOS = "1X2d9ZMcJyK2mN2_gfLAW5NSfMYb17puf"
ID_CORRECTOR = "1o3LFTLye3G3jIazdBVZUX2mbjHWSk5MhQ8lnS37m0Jw"
ID_PRIORIDAD = "1CdrOzXv9Ig69q9cOqlj84bZXcx8wxZEh-XIaAEQytOI"

# Transformación de links para lectura de Pandas
URL_PRECIOS = f"https://docs.google.com/spreadsheets/d/{ID_PRECIOS}/export?format=xlsx"
URL_CORRECTOR = f"https://docs.google.com/spreadsheets/d/{ID_CORRECTOR}/gviz/tq?tqx=out:csv"
URL_PRIORIDAD = f"https://docs.google.com/spreadsheets/d/{ID_PRIORIDAD}/gviz/tq?tqx=out:csv"

# --- FUNCIONES DE LIMPIEZA SEMÁNTICA ---
def normalizar_texto(texto):
    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto.lower().strip()

# --- CARGA AUTOMÁTICA DESDE DRIVE (CACHE DE 15 MINUTOS) ---
@st.cache_data(ttl=900)
def cargar_datos_desde_drive():
    try:
        # 1. Cargar Lista de Precios Oficial (.xlsx original)
        # Saltea las primeras 2 filas basándose en tu estructura original
        df_oficial = pd.read_excel(URL_PRECIOS, skiprows=2)
        df_oficial.columns = ["codigo", "detalle", "moneda", "precio_siva", "precio_civa", "tasa_iva"] + list(df_oficial.columns[6:])
        df_oficial['codigo'] = df_oficial['codigo'].astype(str).str.strip()
        df_oficial['_norm_detalle'] = df_oficial['detalle'].apply(normalizar_texto)
        
        # 2. Cargar Corrector de Errores (Google Sheet)
        try:
            df_err = pd.read_csv(URL_CORRECTOR)
            if 'Texto Cliente' in df_err.columns:
                df_err['_norm_cliente'] = df_err['Texto Cliente'].astype(str).apply(normalizar_texto)
        except Exception as e:
            df_err = pd.DataFrame()
            st.sidebar.warning(f"Aviso en Corrector: {e}")
            
        # 3. Cargar Prioridad de Marcas (Google Sheet)
        try:
            df_mrc = pd.read_csv(URL_PRIORIDAD)
        except Exception as e:
            df_mrc = pd.DataFrame()
            st.sidebar.warning(f"Aviso en Prioridades: {e}")
            
        return df_oficial, df_err, df_mrc, "✅ Datos sincronizados con Google Drive correctamente."
    except Exception as e:
        return None, None, None, f"❌ Error al conectar con Google Drive: {e}. Verificá los accesos de los archivos."

# Ejecutar conexión en caché
df_oficial, df_err, df_mrc, mensaje_status = cargar_datos_desde_drive()
st.sidebar.info(mensaje_status)

# Panel de control lateral para refrescar precios manual si hiciste cambios recién en Drive
if st.sidebar.button("🔄 Forzar actualización de Drive ahora"):
    st.cache_data.clear()
    st.rerun()

# --- MOTOR DE REGLAS DE NEGOCIO ---
def buscar_producto_con_reglas(detalle_cliente, df_precios, df_errores, df_marcas):
    texto_cliente_norm = normalizar_texto(detalle_cliente)
    
    # REGLA 1: Tu tabla manual de Corrección de Errores
    if df_errores is not None and not df_errores.empty and '_norm_cliente' in df_errores.columns:
        match_error = df_errores[df_errores['_norm_cliente'] == texto_cliente_norm]
        if not match_error.empty:
            codigo_corregido = str(match_error.iloc[0]['Codigo Oficial JIELI']).strip()
            resultado_oficial = df_precios[df_precios['codigo'] == codigo_corregido]
            if not resultado_oficial.empty:
                return resultado_oficial.iloc[0], "🎯 Match por Corrección en Drive"

    # REGLA 2: Palabras clave estrictas (Viejo confiable local)
    palabras = [w for w in re.split(r'\W+', texto_cliente_norm) if len(w) > 2]
    if not palabras:
        return None, "❌ Sin coincidencia"
        
    candidatos = df_precios[df_precios['_norm_detalle'].apply(lambda x: all(p in x for p in palabras))]
    
    if candidatos.empty and len(palabras) >= 2:
        candidatos = df_precios[df_precios['_norm_detalle'].str.contains(palabras[0], na=False) & 
                                 df_precios['_norm_detalle'].str.contains(palabras[1], na=False)]
    
    if not candidatos.empty:
        if len(candidatos) == 1:
            return candidatos.iloc[0], "✅ Match Único Directo"
            
        # REGLA 3: Tu tabla manual de Prioridad de Marcas
        if df_marcas is not None and not df_marcas.empty and 'Marca' in df_marcas.columns:
            candidatos = candidatos.copy()
            candidatos['prioridad_orden'] = 999
            
            for _, fila_marca in df_marcas.iterrows():
                marca_norm = normalizar_texto(fila_marca['Marca'])
                candidatos.loc[candidatos['_norm_detalle'].str.contains(marca_norm, na=False), 'prioridad_orden'] = fila_marca['Prioridad']
            
            candidatos = candidatos.sort_values(by='prioridad_orden')
            
        return candidatos.iloc[0], "⭐ Match por Prioridad de Marca"
        
    return None, "❌ Sin coincidencia"


# --- INTERFAZ DE CARGA PARA VENDEDORES ---
if df_oficial is None:
    st.error("Error de inicialización. Por favor revisá que los links de Google Drive estén en modo público (Lector).")
else:
    archivo_cliente = st.file_uploader("Subí el presupuesto del cliente (.xlsx o .csv)", type=["xlsx", "csv"])
    
    if archivo_cliente is not None:
        try:
            df_c = pd.read_csv(archivo_cliente) if archivo_cliente.name.endswith('.csv') else pd.read_excel(archivo_cliente)
            cols = df_c.columns.tolist()
            
            c1, c2 = st.columns(2)
            col_des = c1.selectbox("Columna de Descripción/Producto", cols, index=1 if len(cols)>1 else 0)
            col_cant = c2.selectbox("Columna de Cantidad", cols, index=2 if len(cols)>2 else 0)
            
            if st.button("🚀 Cotizar todo instantáneamente"):
                resultados_finales = []
                
                for _, fila in df_c.iterrows():
                    desc_c = str(fila[col_des])
                    cant_c = fila[col_cant]
                    
                    if desc_c.strip() == "" or "ETAPA" in desc_c.upper() or desc_c == "nan":
                        continue
                        
                    try:
                        cant_c = float(cant_c) if pd.notna(cant_c) else 1.0
                    except:
                        cant_c = 1.0
                        
                    match, metodo = buscar_producto_con_reglas(desc_c, df_oficial, df_err, df_mrc)
                    
                    if match is not None:
                        p_siva = float(match['precio_siva']) if pd.notna(match['precio_siva']) else 0.0
                        p_civa = float(match['precio_civa']) if pd.notna(match['precio_civa']) else 0.0
                        
                        resultados_finales.append({
                            "Pedido Cliente": desc_c,
                            "Cant.": cant_c,
                            "Producto JIELI Sugerido": match['detalle'],
                            "Código": match['codigo'],
                            "Unit. S/IVA": f"${p_siva:,.2f}",
                            "Subtotal S/IVA": p_siva * cant_c,
                            "Subtotal C/IVA": p_civa * cant_c,
                            "Lógica Aplicada": metodo
                        })
                    else:
                        resultados_finales.append({
                            "Pedido Cliente": desc_c,
                            "Cant.": cant_c,
                            "Producto JIELI Sugerido": "❌ NO ENCONTRADO",
                            "Código": "—",
                            "Unit. S/IVA": "$0.00",
                            "Subtotal S/IVA": 0.0,
                            "Subtotal C/IVA": 0.0,
                            "Lógica Aplicada": "❌ Sin Coincidencia"
                        })
                
                df_res = pd.DataFrame(resultados_finales)
                st.subheader("💰 Presupuesto Final Generado")
                st.dataframe(df_res, use_container_width=True)
                
                tot_siva = df_res["Subtotal S/IVA"].sum()
                tot_civa = df_res["Subtotal C/IVA"].sum()
                
                col1, col2 = st.columns(2)
                col1.metric("TOTAL NETO (Sin IVA)", f"${tot_siva:,.2f}")
                col2.metric("TOTAL FACTURADO (Con IVA)", f"${tot_civa:,.2f}")
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_res.to_excel(writer, index=False, sheet_name='Presupuesto')
                
                st.download_button(
                    label="📥 Descargar Cotización en Excel",
                    data=output.getvalue(),
                    file_name="cotizacion_jieli_final.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error(f"Error al procesar el archivo del cliente: {e}")
