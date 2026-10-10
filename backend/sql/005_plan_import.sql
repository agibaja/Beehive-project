-- Requiere 004_admin.sql. Importación atómica e idempotente.
create table if not exists public.admin_space_import (
 request_id uuid primary key, company_id uuid not null,
 payload jsonb not null, result jsonb not null,
 created_at timestamptz not null default now()
);
alter table public.admin_space_import enable row level security;
revoke all on public.admin_space_import from anon,authenticated;

create or replace function public.admin_import_spaces(
 p_company_id uuid,p_request_id uuid,p_floor_id uuid,p_spaces jsonb
) returns jsonb language plpgsql security definer set search_path='' as $$
declare prior public.admin_space_import%rowtype; payload jsonb; item jsonb; saved jsonb; results jsonb:='[]';
begin
 if p_request_id is null or jsonb_typeof(p_spaces) is distinct from 'array'
 or jsonb_array_length(p_spaces)<1 or jsonb_array_length(p_spaces)>200 then
  raise exception 'Selecciona entre 1 y 200 espacios';
 end if;
 lock table public.building,public.floor,public.space,public.employee,public.booking in share row exclusive mode;
 if not exists(select 1 from public.floor f join public.building b on b.id=f.building_id where f.id=p_floor_id and b.company_id=p_company_id) then raise exception 'Planta ajena'; end if;
 payload:=jsonb_build_object('floor_id',p_floor_id,'spaces',p_spaces);
 select * into prior from public.admin_space_import where request_id=p_request_id;
 if found then
  if prior.company_id<>p_company_id or prior.payload<>payload then raise exception 'Solicitud reutilizada con otros datos'; end if;
  return prior.result;
 end if;
 for item in select value from jsonb_array_elements(p_spaces) loop
  if jsonb_typeof(item) is distinct from 'object' then raise exception 'Espacio inválido'; end if;
  saved:=public.admin_save(p_company_id,'space',null,item || jsonb_build_object('floor_id',p_floor_id));
  results:=results || jsonb_build_array(saved);
 end loop;
 saved:=jsonb_build_object('saved',true,'items',results);
 insert into public.admin_space_import(request_id,company_id,payload,result) values(p_request_id,p_company_id,payload,saved);
 return saved;
end; $$;
revoke all on function public.admin_import_spaces(uuid,uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.admin_import_spaces(uuid,uuid,uuid,jsonb) to service_role;
