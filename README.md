# Supervision

Ce dépôt contient les sources d'un logiciel qui permet de faire le suivi et la supervision des températures de nos fermenteurs, à la [Brasserie du Vieux Singe](https://www.vieuxsinge.com). Nous avons eu besoin de mettre en place un outil de ce type pour détecter les problèmes avec notre groupe froid, et être prévenu en cas d'erreurs.

![Capture de l'interface de Grafana](interface.png)

Le script utilise python, et nous le faisons tourner dans un `cronjob` de manière automatique. Lorsque une erreur est détectée, un message nous est envoyé par SMS.

Les données sont collectées localement via des [thermostats connectés](https://github.com/vieuxsinge/stc1000esp) bricolés à partir de STC1000, qui envoient des messages [MQTT](https://mqtt.org/) par Wifi à notre serveur.

Les messages sont ensuite agrégés par [Telegraf](https://www.influxdata.com/time-series-platform/telegraf/) dans une base de données [InfluxDB](https://www.influxdata.com/), et visualisés à travers une installation de [Grafana](https://grafana.com/).

Le script tourne actuellement sur le même serveur que InfluxDB et Grafana.

## Exemples

```
$ /usr/bin/python3 /home/supervision/supervision/analyse_and_alert.py --credentials /home/supervision/credentials.txt
.d8888b.                                              d8b          d8b
d88P  Y88b                                             Y8P          Y8P
Y88b.
"Y888b.   888  888 88888b.   .d88b.  888d888 888  888 888 .d8888b  888  .d88b.  88888b.
   "Y88b. 888  888 888 "88b d8P  Y8b 888P"   888  888 888 88K      888 d88""88b 888 "88b
     "888 888  888 888  888 88888888 888     Y88  88P 888 "Y8888b. 888 888  888 888  888
Y88b  d88P Y88b 888 888 d88P Y8b.     888      Y8bd8P  888      X88 888 Y88..88P 888  888
"Y8888P"   "Y88888 88888P"   "Y8888  888       Y88P   888  88888P' 888  "Y88P"  888  888
                   888
                   888                  https://supervision.vieuxsinge.com
                   888

Recherche d'anomalies pour les fermenteurs f1, f2, f3
par tranches de 180 minutes.

🤷  Aucune température n'est enregistrée par le fermenteur f1.
🎉  Pas d'anomalies detectées pour f2 (consigne à 0°C): 5.95, 5.95, 5.95, 5.95.
🎉  Pas d'anomalies detectées pour f3 (consigne à 0°C): 4.25, 4.25, 4.25, 4.25.
```

## Configuration

Vous pouvez trouver dans le dossier « config » des fichiers de configuration qui sont utiles pour déployer le système.

- [Le fichier crontab](config/crontab) pour que le script se lance tout seul ;
- [La configuration de Grafana au format JSON](config/grafana.json) pour visualiser les données ;
- [La configuration de mosquito](config/mosquito.conf) pour pouvoir recevoir les données ;
- [La configuration de Telegraf](config/telegraf.conf) qui permet d'agréger les données.
