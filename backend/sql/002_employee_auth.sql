alter table public.employee
add column if not exists auth_user_id uuid
references auth.users(id);

create unique index if not exists employee_auth_user_id_unique
on public.employee(auth_user_id)
where auth_user_id is not null;

grant select on public.employee to service_role;
grant select, insert on public.booking to service_role;
grant update (status) on public.booking to service_role;