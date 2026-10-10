from pathlib import Path
import os
import requests
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).with_name('.env'))
st.set_page_config(page_title='Beehive · Organization', page_icon='🐝', layout='wide')
title_col, logo_col = st.columns([5, 1])
with title_col:
    st.title("🐝 Beehive · Organization")
with logo_col:
    st.image("https://raw.githubusercontent.com/agibaja/beehive---assets/main/beehive_logo.png", width=140)
api = os.getenv('BACKEND_URL', 'http://127.0.0.1:8000').rstrip('/')


def call(path, method='get', **kwargs):
    r = requests.request(method, api + '/admin' + path,
        headers={'Authorization':'Bearer '+st.session_state['token']}, timeout=90, **kwargs)
    if r.status_code == 401:
        st.session_state.pop('token', None)
        st.error('Sesión caducada. Vuelve a iniciar sesión.'); st.stop()
    if not r.ok:
        try: detail = r.json().get('detail', 'Operación fallida')
        except ValueError: detail = 'El backend no respondió correctamente'
        raise ValueError(str(detail))
    return r.json()


if 'token' not in st.session_state:
    st.write('Accede con la cuenta de administración de Supabase Auth.')
    with st.form('login'):
        email = st.text_input('Correo')
        password = st.text_input('Contraseña', type='password')
        submit = st.form_submit_button('Entrar')
    if submit:
        try:
            client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
            session = client.auth.sign_in_with_password({'email':email,'password':password}).session
            st.session_state['token'] = session.access_token
            call('/catalog')
            st.rerun()
        except Exception:
            st.session_state.pop('token', None)
            st.error('No se pudo iniciar sesión. Comprueba la cuenta, su rol y la configuración.')
    st.stop()

if st.sidebar.button('Cerrar sesión'):
    st.session_state.clear(); st.rerun()

try:
    data = call('/catalog')
except (ValueError, requests.RequestException) as exc:
    st.error(str(exc)); st.stop()


def label(row):
    return str(row.get('name') or row.get('floor_number') or row.get('number') or row['id'])


def choose(rows, text, key):
    if not rows:
        st.info('Crea primero la entidad superior.'); return None
    return st.selectbox(text, rows, format_func=label, key=key)


mode = st.sidebar.radio('Gestión', ['Edificios', 'Plantas', 'Espacios', 'Planos'])
if mode == 'Planos':
    from plan_workflow import show_plan_workflow
    show_plan_workflow(data, call)
    st.stop()

entity = {'Edificios':'building','Plantas':'floor','Espacios':'space'}[mode]
rows = data[{'building':'buildings','floor':'floors','space':'spaces'}[entity]]
parent = None
if entity in ('floor','space'):
    b = choose(data['buildings'],'Edificio','building_filter')
    if not b: st.stop()
    if entity == 'floor':
        parent = ('building_id',b['id']); rows=[r for r in rows if r['building_id']==b['id']]
    else:
        f=choose([r for r in data['floors'] if r['building_id']==b['id']],'Planta','floor_filter')
        if not f: st.stop()
        parent=('floor_id',f['id']); rows=[r for r in rows if r['floor_id']==f['id']]

st.subheader(mode)
if rows: st.dataframe(rows, hide_index=True)
action=st.radio('Acción',['Añadir','Editar'],horizontal=True)
selected=choose(rows,'Selecciona el registro','edit_'+entity) if action=='Editar' else None
if action=='Editar' and not selected: st.stop()
initial=selected or {}
columns=[c for c in data['columns'] if c['table']==entity]
ignored={'id','company_id','floor_id','building_id','created_at','updated_at'}
with st.form('save_'+entity+'_'+str(initial.get('id','new'))):
    values={}
    if parent: values[parent[0]]=parent[1]
    for c in columns:
        name=c['name']; typ=c['type']
        if name in ignored: continue
        old=initial.get(name)
        if name=='type': values[name]=st.selectbox('Tipo',['desk','meeting_room'],index=1 if old=='meeting_room' else 0)
        elif typ=='boolean': values[name]=st.checkbox(name,value=bool(old))
        elif typ in ('integer','smallint','bigint'):
            values[name]=int(st.number_input(name,value=int(old if old is not None else (1 if name=='capacity' else 0)),step=1))
        elif typ in ('numeric','real','double precision'):
            v=st.text_input(name,value='' if old is None else str(old))
            values[name]=v or None
        elif typ in ('text','character varying','uuid','USER-DEFINED'):
            v=st.text_input(name,value=str(old) if old is not None else '')
            if v: values[name]=v.strip()
            elif c['nullable']=='YES': values[name]=None
        else:
            st.caption(f'{name}: campo {typ} conservado; no editable en esta versión.')
    st.caption('La edición se bloquea si el espacio tiene reservas activas o futuras. No se borran espacios ni se cambian asignaciones.')
    save=st.form_submit_button('Guardar')
if save:
    try:
        result=call('/'+entity+'/save','post',json={'id':initial.get('id'),'values':values})
        st.success('Registro guardado.'); st.json(result)
    except (ValueError,requests.RequestException) as exc: st.error(str(exc))
