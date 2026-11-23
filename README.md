# Ritardi Combo — nuova versione

Copia autonoma del progetto originale `edbass1/ritardi-combo`, con le nuove funzioni aggiuntive in sviluppo. Il repository originale resta separato e invariato.

La pagina pubblica è cifrata. GitHub Actions genera i dati, li cifra con la password del secret `NEWCOMBO_PASS` e pubblica solo la maschera di login (`actions/upload-pages-artifact` e `actions/deploy-pages`). `index.html` e i file `.txt` non vanno committati.

Prima del primo deploy servono due impostazioni nel repository:

1. Secret `NEWCOMBO_PASS` in Settings → Secrets and variables → Actions.
2. Settings → Pages → Source = GitHub Actions.
