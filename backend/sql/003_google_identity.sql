alter table public.employee
add column if not exists google_sub text;

create unique index if not exists employee_google_sub_unique
on public.employee(google_sub)
where google_sub is not null;