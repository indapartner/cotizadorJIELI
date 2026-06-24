import streamlit as st
import pandas as pd
import re
import unicodedata

st.set_page_config(layout="wide")
st.title("⚡ Cotizador JIELI - Versión Blindada")

def normalizar(texto):
    if not isinstance(texto, str): return ""
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn").lower().strip()

# --- PASO 1: CARGA MAESTRA ---
archivo_precios = st.file_uploader("Subí tu lista de precios (.xlsx)", type=["xlsx"])
df_oficial = None

if archivo_precios:
    # Ajustamos header=1 porque tus archivos tienen una fila de título arriba de los encabezados
    df_oficial = pd.read_excel(archivo_precios, header=1)
    
    # NORMALIZACIÓN AUTOMÁTICA: Convertimos todos los nombres de columnas a minúsculas y sin espacios
    df_oficial.columns = [str(c).lower().strip().replace(" ", "_") for c in df_oficial.columns]
    
    # NORMALIZAMOS LA COLUMNA DE DETALLE (Buscamos la que contenga 'detalle' o 'descripcion')
    col_detalle = [c for c in df_oficial.columns if 'detalle' in c or 'descripcion' in c][0]
    df_oficial['norm_detalle'] = df_oficial[col_detalle].apply(normalizar)
    
    st.success(f"Catálogo cargado correctamente. Columnas detectadas: {list(df_oficial.columns)}")

# --- PASO 2: PROCESAR PEDIDO ---
if df_oficial is not None:
    archivo_cliente = st.file_uploader("Subí el pedido del cliente", type=["xlsx", "csv"])
    
    if archivo_cliente:
        df_c = pd.read_csv(archivo_cliente) if archivo_cliente.name.endswith('.csv') else pd.read_excel(archivo_cliente)
        
        # Permitimos elegir las columnas dinámicamente para no depender de nombres fijos
        col_des = st.selectbox("Elegí la columna de PRODUCTO/DETALLE:", df_c.columns)
        
        if st.button("Procesar"):
            res = []
            for _, fila in df_c.iterrows():
                busqueda = normalizar(fila[col_des])
                match = df_oficial[df_oficial['norm_detalle'].str.contains(busqueda, na=False, regex=False)]
                
                if not match.empty:
                    res.append({"Solicitado": fila[col_des], "Encontrado": match.iloc[0][col_detalle], "Código": match.iloc[0]['código']})
                else:
                    res.append({"Solicitado": fila[col_des], "Encontrado": "NO ENCONTRADO", "Código": "-"})
            
            st.dataframe(pd.DataFrame(res))
