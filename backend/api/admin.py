"""Administración autenticada mediante Supabase Auth; Calendar no cambia."""
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID, uuid4
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError
from api.database import get_database

router = APIRouter(prefix='/admin', tags=['Administración'])
bearer = HTTPBearer()
logger = logging.getLogger(__name__)


def admin_user(credentials=Depends(bearer), db=Depends(get_database)):
    try:
        user = db.auth.get_user(credentials.credentials).user
    except Exception:
        raise HTTPException(401, 'Sesión inválida o caducada')
    rows = db.table('employee').select('id,company_id,name,role').eq('auth_user_id', str(user.id)).limit(1).execute().data if user else []
    if not rows or rows[0]['role'] != 'admin' or not rows[0].get('company_id'):
        raise HTTPException(403, 'Usuario no vinculado a un administrador')
    return rows[0]


def catalog(db, admin):
    return db.rpc('admin_catalog', {'p_company_id': admin['company_id']}).execute().data


@router.get('/catalog')
def get_catalog(admin=Depends(admin_user), db=Depends(get_database)):
    return catalog(db, admin)


class Save(BaseModel):
    id: UUID | None = None
    values: dict = Field(default_factory=dict)


@router.post('/{table}/save')
def save(table: str, payload: Save, admin=Depends(admin_user), db=Depends(get_database)):
    if table not in ('building','floor','space'):
        raise HTTPException(404, 'Entidad desconocida')
    try:
        return db.rpc('admin_save', {'p_company_id': admin['company_id'], 'p_table': table,
            'p_id': str(payload.id) if payload.id else None, 'p_values': payload.values}).execute().data
    except Exception:
        logger.exception('No se pudo guardar %s', table)
        raise HTTPException(409, 'No se pudo guardar. Revisa los campos, el propietario y las reservas activas o futuras.')


@router.post('/floors/{floor_id}/plans')
async def upload_plan(floor_id: UUID, file: UploadFile = File(...), admin=Depends(admin_user), db=Depends(get_database)):
    data = catalog(db, admin)
    if str(floor_id) not in {str(f['id']) for f in data['floors']}:
        raise HTTPException(404, 'Planta desconocida')
    raw = await file.read(15 * 1024 * 1024 + 1)
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, 'Límite: 15 MB')
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.format not in ('PNG','JPEG') or im.width * im.height > 25_000_000:
                raise ValueError()
            im.load()
            output = BytesIO(); im.convert('RGB').save(output, format='PNG')
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(422, 'Usa un PNG/JPG válido de hasta 25 millones de píxeles')
    path = f"{admin['company_id']}/{floor_id}/{uuid4()}.png"
    bucket = db.storage.from_('beehive-plans')
    bucket.upload(path, output.getvalue(), {'content-type':'image/png'})
    try:
        return db.table('floor_plan').insert({'floor_id':str(floor_id), 'name':(file.filename or 'Plano')[:200], 'storage_path':path}).execute().data[0]
    except Exception:
        bucket.remove([path]); raise


@router.get('/plans/{plan_id}')
def view_plan(plan_id: UUID, admin=Depends(admin_user), db=Depends(get_database)):
    plans = catalog(db, admin)['plans']
    plan = next((p for p in plans if str(p['id']) == str(plan_id)), None)
    if not plan:
        raise HTTPException(404, 'Plano desconocido')
    return db.storage.from_('beehive-plans').create_signed_url(plan['storage_path'], 300)


class ImportSpaces(BaseModel):
    request_id: UUID
    floor_id: UUID
    spaces: list[dict] = Field(min_length=1, max_length=200)


@router.post('/spaces/import')
def import_spaces(payload: ImportSpaces, admin=Depends(admin_user), db=Depends(get_database)):
    try:
        return db.rpc('admin_import_spaces', {
            'p_company_id': admin['company_id'],
            'p_request_id': str(payload.request_id),
            'p_floor_id': str(payload.floor_id),
            'p_spaces': payload.spaces,
        }).execute().data
    except Exception:
        logger.exception('No se pudo importar espacios')
        raise HTTPException(409, 'No se guardó la importación. Revisa capacidad, campos obligatorios y empleado asignado. Puedes reintentar sin duplicar esta solicitud.')
