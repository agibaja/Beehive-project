-- Ejecutar después de 001, 002 y 003. No altera reservas existentes.
create table if not exists public.floor_plan (
 id uuid primary key default gen_random_uuid(), floor_id uuid not null references public.floor(id),
 name text not null, storage_path text not null, created_at timestamptz not null default now()
);
alter table public.floor_plan enable row level security;
insert into storage.buckets(id,name,public) values('beehive-plans','beehive-plans',false) on conflict(id) do nothing;
grant select,insert,update on public.building,public.floor,public.space,public.floor_plan to service_role;

create or replace function public.admin_catalog(p_company_id uuid)
returns jsonb language sql stable security definer set search_path='' as $$
select jsonb_build_object(
 'buildings',(select coalesce(jsonb_agg(to_jsonb(b)),'[]') from public.building b where b.company_id=p_company_id),
 'floors',(select coalesce(jsonb_agg(to_jsonb(f)),'[]') from public.floor f join public.building b on b.id=f.building_id where b.company_id=p_company_id),
 'spaces',(select coalesce(jsonb_agg(to_jsonb(s)),'[]') from public.space s join public.floor f on f.id=s.floor_id join public.building b on b.id=f.building_id where b.company_id=p_company_id),
 'plans',(select coalesce(jsonb_agg(to_jsonb(p)),'[]') from public.floor_plan p join public.floor f on f.id=p.floor_id join public.building b on b.id=f.building_id where b.company_id=p_company_id),
 'columns',(select jsonb_agg(jsonb_build_object('table',table_name,'name',column_name,'type',data_type,'nullable',is_nullable,'default',column_default)) from information_schema.columns where table_schema='public' and table_name in ('building','floor','space'))
); $$;

create or replace function public.admin_save(p_company_id uuid,p_table text,p_id uuid,p_values jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare k text; cols text; vals text; setters text; result jsonb; parent_id uuid; row_id uuid;
begin
 if p_table not in ('building','floor','space') or jsonb_typeof(p_values) <> 'object' then raise exception 'Entrada inválida'; end if;
 lock table public.building,public.floor,public.space,public.employee,public.booking in share row exclusive mode;
 -- Misma estrategia de bloqueo que el guardado del optimizador.
 if p_values ? 'id' or p_values ? 'company_id' then raise exception 'No se pueden modificar identificadores'; end if;
 if p_table='building' then
  if p_id is not null and not exists(select 1 from public.building where id=p_id and company_id=p_company_id) then raise exception 'Edificio ajeno'; end if;
  p_values:=p_values || jsonb_build_object('company_id',p_company_id);
 elsif p_table='floor' then
  parent_id:=(p_values->>'building_id')::uuid;
  if not exists(select 1 from public.building where id=parent_id and company_id=p_company_id) then raise exception 'Edificio ajeno'; end if;
  if p_id is not null and not exists(select 1 from public.floor f join public.building b on b.id=f.building_id where f.id=p_id and b.company_id=p_company_id) then raise exception 'Planta ajena'; end if;
 else
  parent_id:=(p_values->>'floor_id')::uuid;
  if not exists(select 1 from public.floor f join public.building b on b.id=f.building_id where f.id=parent_id and b.company_id=p_company_id) then raise exception 'Planta ajena'; end if;
  if p_id is not null and not exists(select 1 from public.space s join public.floor f on f.id=s.floor_id join public.building b on b.id=f.building_id where s.id=p_id and b.company_id=p_company_id) then raise exception 'Espacio ajeno'; end if;
  if p_id is not null and exists(select 1 from public.booking where space_id=p_id and status in ('assigned','pending') and end_time>now()) then raise exception 'El espacio tiene reservas activas o futuras. No se permite editarlo todavía.'; end if;
  if coalesce(p_values->>'type','') not in ('desk','meeting_room') or coalesce((p_values->>'capacity')::int,0)<1 then raise exception 'Tipo o capacidad inválidos'; end if;
  if p_values->>'type'='desk' and (p_values->>'capacity')::int<>1 then raise exception 'Un puesto debe tener capacidad 1'; end if;
  if coalesce((p_values->>'is_fixed')::boolean,false) then
   if not exists(select 1 from public.employee where id=(p_values->>'assigned_employee_id')::uuid and company_id=p_company_id) then raise exception 'Selecciona un empleado de la empresa para el puesto fijo'; end if;
  elsif p_values->>'assigned_employee_id' is not null then raise exception 'Un espacio no fijo no debe tener propietario'; end if;
 end if;
 for k in select jsonb_object_keys(p_values) loop
  if not exists(select 1 from information_schema.columns where table_schema='public' and table_name=p_table and column_name=k) then raise exception 'Columna desconocida: %',k; end if;
 end loop;
 select string_agg(format('%I',key),','),string_agg(format('r.%I',key),','),string_agg(format('%I=r.%I',key,key),',') into cols,vals,setters from jsonb_object_keys(p_values) key;
 if p_id is null then
  row_id:=gen_random_uuid();
  execute format('with r as (select * from jsonb_populate_record(null::public.%I,$1)), saved as (insert into public.%I(id,%s) select $2,%s from r returning *) select to_jsonb(saved) from saved',p_table,p_table,cols,vals) into result using p_values,row_id;
 else
  execute format('with r as (select * from jsonb_populate_record(null::public.%I,$1)), saved as (update public.%I t set %s from r where t.id=$2 returning t.*) select to_jsonb(saved) from saved',p_table,p_table,setters) into result using p_values,p_id;
 end if;
 return result;
end; $$;
revoke all on function public.admin_catalog(uuid) from public,anon,authenticated;
revoke all on function public.admin_save(uuid,text,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.admin_catalog(uuid) to service_role;
grant execute on function public.admin_save(uuid,text,uuid,jsonb) to service_role;
