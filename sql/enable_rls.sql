alter table public."user" enable row level security;
alter table public.quiz_session enable row level security;
alter table public.quiz_question enable row level security;
alter table public.flashcard_deck enable row level security;
alter table public.super_note enable row level security;

revoke all on table public."user" from anon, authenticated;
revoke all on table public.quiz_session from anon, authenticated;
revoke all on table public.quiz_question from anon, authenticated;
revoke all on table public.flashcard_deck from anon, authenticated;
revoke all on table public.super_note from anon, authenticated;

revoke all on all sequences in schema public from anon, authenticated;

alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;