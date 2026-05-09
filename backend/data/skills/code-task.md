---
name: code-task
description: Disciplined coding workflow — read, plan, edit, verify
when_to_use: the user asks you to write, edit, or debug code
---

# Recipe — code-task

Whenever you touch code:

1. **Read before writing.** Use `read_file` / `list_files` to understand
   structure first. Never invent file paths.
2. **Plan in 3-5 lines.** Before any edit, state: "I'll change X in file Y
   to do Z." Show the plan to the user only if it's non-trivial.
3. **Smallest diff that works.** Don't refactor unrelated code in the same
   change. Don't add comments that just narrate the code.
4. **Verify with `python_repl`.** For pure-Python logic, run a quick check
   in the REPL before claiming the change is done.
5. **Report**: state what changed (file:line) + what you verified + any
   follow-ups still needed. No gratuitous summaries of the whole file.

If the task touches more than 3 files, propose a numbered plan first and
wait for the user to approve before editing.
