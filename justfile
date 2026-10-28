# Backwards-compatible aliases; Taskfile.yml is the command source of truth.
default:
    @task --list

fmt:
    task fmt

check:
    task check

smoke:
    task smoke
