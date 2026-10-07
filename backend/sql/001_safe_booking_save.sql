-- 1. Leer los datos que necesita el optimizador.
create or replace function public.optimizer_snapshot(
    p_company_id uuid,
    p_cutoff timestamptz
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select jsonb_build_object(
        'company_id', p_company_id,
        'cutoff', p_cutoff,

        'buildings', (
            select coalesce(jsonb_agg(to_jsonb(b) order by b.id), '[]'::jsonb)
            from public.building b
            where b.company_id = p_company_id
        ),

        'floors', (
            select coalesce(jsonb_agg(to_jsonb(f) order by f.id), '[]'::jsonb)
            from public.floor f
            join public.building b on b.id = f.building_id
            where b.company_id = p_company_id
        ),

        'spaces', (
            select coalesce(jsonb_agg(to_jsonb(s) order by s.id), '[]'::jsonb)
            from public.space s
            join public.floor f on f.id = s.floor_id
            join public.building b on b.id = f.building_id
            where b.company_id = p_company_id
        ),

        'employees', (
            select coalesce(jsonb_agg(to_jsonb(e) order by e.id), '[]'::jsonb)
            from public.employee e
            where e.company_id = p_company_id
        ),

        'bookings', (
            select coalesce(jsonb_agg(to_jsonb(r) order by r.id), '[]'::jsonb)
            from public.booking r
            join public.employee e on e.id = r.employee_id
            where e.company_id = p_company_id
              and r.status in ('pending', 'assigned')
              and r.end_time > p_cutoff
        )
    );
$$;


-- 2. Guardar una propuesta completa.
create or replace function public.save_optimizer_proposal(
    p_snapshot jsonb,
    p_assignments jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_company_id uuid;
    v_cutoff timestamptz;
    v_now timestamptz;
    v_current jsonb;
    v_booking public.booking%rowtype;
    v_space public.space%rowtype;
    v_space_id uuid;
    v_type text;
    v_updated integer := 0;
begin
    if jsonb_typeof(p_snapshot) is distinct from 'object'
       or jsonb_typeof(p_assignments) is distinct from 'object' then
        raise exception 'Formato de propuesta inválido.';
    end if;

    v_company_id := (p_snapshot->>'company_id')::uuid;
    v_cutoff := (p_snapshot->>'cutoff')::timestamptz;

    if v_company_id is null or v_cutoff is null then
        raise exception 'Falta empresa o fecha de lectura.';
    end if;

    -- Bloqueo breve durante el guardado, no durante la optimización.
    -- Evita escrituras concurrentes mientras comprobamos y actualizamos.
    lock table
        public.building,
        public.floor,
        public.space,
        public.employee,
        public.booking
    in share row exclusive mode;

    v_now := clock_timestamp();

    -- Usamos el mismo cutoff que en la lectura original.
    v_current := public.optimizer_snapshot(v_company_id, v_cutoff);

    if v_current is distinct from p_snapshot then
        raise exception
            'Los datos cambiaron. Vuelve a leer y optimizar antes de guardar.';
    end if;

    -- La propuesta debe contener exactamente las reservas de la lectura.
    if (
        select count(*) from jsonb_object_keys(p_assignments)
    ) <> jsonb_array_length(p_snapshot->'bookings') then
        raise exception 'La propuesta no contiene todas las reservas.';
    end if;

    if exists (
        select 1
        from jsonb_array_elements(p_snapshot->'bookings') r
        where not (p_assignments ? (r->>'id'))
    ) then
        raise exception 'Faltan reservas en la propuesta.';
    end if;

    for v_booking in
        select r.*
        from public.booking r
        join public.employee e on e.id = r.employee_id
        where e.company_id = v_company_id
          and r.status in ('pending', 'assigned')
          and r.end_time > v_cutoff
        order by r.id
    loop
        -- Un valor JSON null representa una solicitud sin atender.
        v_space_id :=
            (p_assignments->>v_booking.id::text)::uuid;

        if v_booking.status = 'assigned'
           and v_space_id is null then
            raise exception
                'La reserva % ya estaba asignada y no puede perder su espacio.',
                v_booking.id;
        end if;

        -- El tiempo puede haber avanzado durante el cálculo.
        if v_booking.end_time <= v_now then
            raise exception
                'La reserva % terminó durante el cálculo. Recalcula.',
                v_booking.id;
        end if;

        if v_booking.status = 'pending'
           and v_booking.start_time <= v_now then
            raise exception
                'La solicitud % caducó durante el cálculo. Recalcula.',
                v_booking.id;
        end if;

        if (
            coalesce(v_booking.is_locked, false)
            or (
                v_booking.status = 'assigned'
                and v_booking.start_time <= v_now + interval '12 hours'
            )
        ) and v_space_id is distinct from v_booking.space_id then
            raise exception
                'La reserva % está bloqueada y no puede cambiar.',
                v_booking.id;
        end if;

        if v_space_id is null then
            continue;
        end if;

        select s.* into v_space
        from public.space s
        join public.floor f on f.id = s.floor_id
        join public.building b on b.id = f.building_id
        where s.id = v_space_id
          and b.company_id = v_company_id;

        if not found then
            raise exception
                'Espacio inexistente o ajeno a la empresa: %.',
                v_space_id;
        end if;

        v_type := v_booking.request_type;

        -- Compatibilidad con reservas antiguas cuyo tipo se deduce
        -- del espacio que tenían asignado.
        if v_type is null and v_booking.space_id is not null then
            select s.type into v_type
            from public.space s
            where s.id = v_booking.space_id;
        end if;

        if v_type is null
           or v_type not in ('desk', 'meeting_room')
           or v_space.type is distinct from v_type
           or v_booking.people_count is null
           or v_booking.people_count < 1
           or v_space.capacity is null
           or v_space.capacity < v_booking.people_count
           or (v_type = 'desk' and v_booking.people_count <> 1)
           or (
               coalesce(v_booking.needs_monitor, false)
               and not coalesce(v_space.has_monitor, false)
           )
           or (
               coalesce(v_booking.needs_videoconference, false)
               and not coalesce(v_space.has_videoconference, false)
           )
           or (
               coalesce(v_booking.needs_whiteboard, false)
               and not coalesce(v_space.has_whiteboard, false)
           )
           or (
               coalesce(v_booking.needs_accessibility, false)
               and not coalesce(v_space.is_accessible, false)
           )
           or (
               coalesce(v_space.is_fixed, false)
               and v_space.assigned_employee_id
                   is distinct from v_booking.employee_id
           ) then
            raise exception
                'El espacio % no cumple los requisitos de la reserva %.',
                v_space_id, v_booking.id;
        end if;
    end loop;

    -- Revisar conflictos en la propuesta completa antes de actualizar.
    if exists (
        with proposed as (
            select
                r.id,
                r.employee_id,
                r.start_time,
                r.end_time,
                (p_assignments->>r.id::text)::uuid as space_id
            from public.booking r
            join public.employee e on e.id = r.employee_id
            where e.company_id = v_company_id
              and r.status in ('pending', 'assigned')
              and r.end_time > v_cutoff
        )
        select 1
        from proposed a
        join proposed b on a.id < b.id
        join public.space sa on sa.id = a.space_id
        join public.space sb on sb.id = b.space_id
        where a.start_time < b.end_time
          and b.start_time < a.end_time
          and (
              a.space_id = b.space_id
              or (
                  a.employee_id = b.employee_id
                  and sa.type = sb.type
              )
          )
    ) then
        raise exception 'La propuesta contiene reservas incompatibles en horario.';
    end if;

    -- Todas las comprobaciones han pasado: actualizar solo cambios.
    update public.booking r
    set
        space_id = (p_assignments->>r.id::text)::uuid,
        status = case
            when p_assignments->>r.id::text is null then 'pending'
            else 'assigned'
        end
    from public.employee e
    where e.id = r.employee_id
      and e.company_id = v_company_id
      and r.status in ('pending', 'assigned')
      and r.end_time > v_cutoff
      and (
          r.space_id is distinct from
              (p_assignments->>r.id::text)::uuid
          or r.status is distinct from (
              case
                  when p_assignments->>r.id::text is null then 'pending'
                  else 'assigned'
              end
          )
      );

    get diagnostics v_updated = row_count;

    return jsonb_build_object(
        'saved', true,
        'updated', v_updated
    );
end;
$$;


-- Estas funciones son exclusivas del backend.
revoke all on function public.optimizer_snapshot(uuid, timestamptz)
from public, anon, authenticated;

revoke all on function public.save_optimizer_proposal(jsonb, jsonb)
from public, anon, authenticated;

grant execute on function public.optimizer_snapshot(uuid, timestamptz)
to service_role;

grant execute on function public.save_optimizer_proposal(jsonb, jsonb)
to service_role;