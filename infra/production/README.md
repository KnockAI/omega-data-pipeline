# Production foundation handoff

Credential-independent preparation for the OMEGA runtime. Production
promotion remains founder-authorized.

Required injection variables are listed in `env.example`. `preflight.sh`
validates presence, migration mode, queue connectivity, backup destination,
and rollback target without executing a deployment. `rollback.sh` is
intentionally guarded and prints the exact authorized command.

Activation sequence:

```text
inject secrets -> preflight.sh -> apply migrations in staging/shadow
-> run smoke/recovery checks -> deploy with deploy.sh -> verify -> shadow
```
