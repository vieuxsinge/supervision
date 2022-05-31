# coding=utf-8

from influxdb import InfluxDBClient
import ovh
import pathlib
from pprint import pprint
from sty import fg, bg, ef, rs, Style, RgbFg


ASCII_ART = """
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
"""


def unpack(s):
    return ", ".join(map(str, s))


def unpack_and_round(values):
    return unpack([round(d, 2) for d in values])


class Anomaly(Exception):
    def __init__(self, message, context={}):
        self.message = message
        self.context = context
        super().__init__(message)


class Analyser:
    def __init__(
        self,
        host="localhost",
        port="8086",
        sms_credentials=(),
        max_temperature=23,
        verbose=False,
        dry_run=False,
        send_test_message=False,
        phone_numbers=None
    ):
        self._client = InfluxDBClient(host, 8086)
        self.sms_credentials = sms_credentials
        self.verbose = verbose
        self.dry_run = dry_run
        self.max_temperature = max_temperature
        self.send_test_message = send_test_message
        self.phone_numbers = phone_numbers or []

        if self.verbose:

            def _query(*args, **kwargs):
                print(kwargs["query"])
                return self._client._query(*args, **kwargs)

            self._client._query = self._client.query
            self._client.query = _query

    def log(self, channel, message=""):
        fg.orange = Style(RgbFg(255, 150, 50))
        icons = {
            "logo": (fg.white + ASCII_ART, fg.rs),
            "header": (" " + ef.bold, rs.bold_dim),
            "subheader": (ef.i + fg.white + " ", fg.rs + rs.i + "\r\n"),
            "info": ("  " + "🤷 " + fg.white, fg.rs),
            "error": ("  " + "💥 " + fg.orange, fg.rs),
            "check": ("  " + "🎉 " + fg.green, fg.rs),
            "phone": ("  " + "📱", ""),
            "end": ("\r\n", ""),
        }
        before, after = icons.get(channel, "")
        print(f"{before} {message} {after}")

    def run(self, fermenters, date, group_time):
        self.log("logo")
        self.log(
            "header", f"Recherche d'anomalies pour les fermenteurs {unpack(fermenters)}"
        )

        msg = ""
        if date != "now":
            msg += f"pour la date {date}, "
        msg += f"par tranches de {group_time} minutes."
        self.log("subheader", msg)

        for fermenter in fermenters:
            try:
                context = self.analyse(
                    fermenter, start_time=date, group_time=group_time
                )
            except Anomaly as e:
                self.send_alert(e)
            else:
                self.log(
                    "check",
                    f"Pas d'anomalies detectées pour {fermenter} (consigne à {context['setpoint']}°C): {unpack_and_round(context['temperatures'])}.",
                )

        if self.send_test_message:
            self.send_alert(Anomaly(
                ("Ceci est un message de test envoyé par le système "
                 "de supervision de la brasserie")))

        self.log("end")

    def get_temperatures(self, fermenter, start_time, group_time, tries=2):
        if start_time == "now":
            start_time = "now()"

        since = group_time * 3

        query = f"""
        SELECT mean("value") FROM "autogen"."mqtt_consumer_float"
        WHERE ("topic" = 'fermenters/{fermenter}/temperature')
        AND time >=  {start_time} -{since}m
        AND time <= {start_time}
        GROUP BY time({group_time}m) fill(previous)
        """

        response = self._client.query(query=query, database="telegraf")
        if not response:
            if tries:
                return self.get_temperatures(
                    fermenter, start_time, group_time * 2, tries - 1
                )
            else:
                raise Anomaly("no-temperatures", {"fermenter": fermenter})

        temperatures = [temp for _, temp in response.raw["series"][0]["values"] if temp]
        if not temperatures:
            raise Anomaly("no-temperatures", {"fermenter": fermenter})
        return temperatures

    def get_setpoint(self, fermenter):
        query = f"""
        SELECT last("value")
        FROM "autogen"."mqtt_consumer_float"
        WHERE ("topic" = 'fermenters/{fermenter}/setpoint')
        """
        response = self._client.query(query=query, database="telegraf")
        return response.raw["series"][0]["values"][0][-1]

    def get_cooling_info(self, fermenter, start_time="now"):
        if start_time == "now":
            start_time = "now()"

        query = f"""
        SELECT last("value")
        FROM "autogen"."mqtt_consumer_float"
        WHERE ("topic" = 'fermenters/{fermenter}/cooling')
        AND time <= {start_time}
        """
        response = self._client.query(query=query, database="telegraf")
        return response.raw["series"][0]["values"][0][1]

    def analyse(self, fermenter, start_time, group_time):
        all_temperatures = self.get_temperatures(fermenter, start_time, group_time)
        # Do the computation on the last 6 values (= last 30mn)
        context = dict(
            fermenter=fermenter,
            temperatures=all_temperatures,
            is_cooling=self.get_cooling_info(fermenter, start_time),
            setpoint=self.get_setpoint(fermenter),
            max_temp=self.max_temperature,
        )
        if self.verbose:
            pprint(context)
        self.check_temperature_convergence(**context)
        self.check_temperature_max(**context)
        return context

    def check_temperature_max(self, fermenter, temperatures, max_temp, *args, **kwargs):
        # Did we exceed the max?
        if any([temp > max_temp for temp in temperatures]):
            raise Anomaly(
                "temperature-exceeds-max",
                {"fermenter": fermenter, "temperatures": temperatures},
            )

    def check_temperature_convergence(
        self, fermenter, temperatures, is_cooling, setpoint, *args, **kwargs
    ):
        is_decreasing = all(i >= j for i, j in zip(temperatures, temperatures[1:]))
        is_increasing = any(i < j for i, j in zip(temperatures, temperatures[1:]))

        if setpoint < temperatures[-1]:
            if (
                is_increasing
                and is_cooling
                and (temperatures[-1] - temperatures[0]) > 0.5
            ):
                raise Anomaly(
                    "temperature-rising",
                    {
                        "fermenter": fermenter,
                        "temperatures": temperatures,
                        "setpoint": setpoint,
                    },
                )
        elif (
            setpoint > temperatures[-1]
            and is_decreasing
            and temperatures[0] - temperatures[-1] > 0.5
        ):
            raise Anomaly(
                "temperature-falling",
                {
                    "fermenter": fermenter,
                    "temperatures": temperatures,
                    "setpoint": setpoint,
                },
            )

    def send_alert(self, anomaly):
        context = anomaly.context
        anomaly_type = anomaly.message

        send = True
        message_type = "error"

        if anomaly_type == "temperature-rising":
            message = (
                f"""Le fermenteur {context['fermenter']} grimpe en température """
                f"""({unpack_and_round(context['temperatures'])}), alors qu'il est """
                f"""sensé refroidir (consigne à {context['setpoint']}°C)!"""
            )
        elif anomaly_type == "temperature-falling":
            message = (
                f"Attention, le fermenteur {context['fermenter']} descends en temperature "
                f"({unpack_and_round(context['temperatures'])}) alors qu'il est sensé monter."
            )
        elif anomaly_type == "no-temperatures":
            message = f"Aucune température n'est enregistrée par le fermenteur {context['fermenter']}."
            send = False
            message_type = "info"
        else:
            message = anomaly_type

        self.log(message_type, message)
        if send and not self.dry_run:
            self.send_multiple_sms(message)

    def send_multiple_sms(self, message):
        (app_name, app_key, app_secret, key) = self.sms_credentials
        client = ovh.Client(
            endpoint='ovh-eu',
            application_key=app_key,
            application_secret=app_secret,
            consumer_key=key,
        )

        result = client.post(f'/sms/{app_name}/jobs',
            message=message,
            noStopClause=True,
            receivers=self.phone_numbers,
            senderForResponse=True,
        )
        self.log("phone", f"SMS envoyé à {self.phone_numbers}")


def parse_credentials(filename):
    text = pathlib.Path(filename).read_text("utf-8").strip("\n")
    return text.split(":")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyse les données dans influxdb à la recherche d'anomalies"
    )

    parser.add_argument(
        "--fermenters",
        dest="fermenters",
        help="Fermenteurs à utiliser.",
        default="f1,f2,f3,f4",
    )

    parser.add_argument(
        "--server",
        dest="server",
        help="Serveur auquel se connecter.",
        default="supervision.vieuxsinge.com",
    )

    parser.add_argument(
        "--max-temperature",
        dest="max_temperature",
        type=int,
        help="Température maximum autorisée dans les fermenteurs",
        default=200,
    )

    parser.add_argument(
        "-c",
        "--credentials",
        dest="credentials",
        default="credentials.txt",
        help="Chemin vers le fichier contenant les identifiants du service SMS.",
    )

    parser.add_argument(
        "--phone-numbers",
        dest="phone_numbers",
        nargs='+',
        required=True,
        help='La liste des numéros de téléphone (avec leur préfixes) où envoyer les messages'
    )

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Ne pas envoyer les SMS.",
    )

    parser.add_argument(
        "--send-test-message",
        dest="send_test_message",
        action="store_true",
        default=False,
        help="Envoi un message SMS pour tester le fonctionnement.",
    )

    parser.add_argument(
        "--date", dest="date", default="now", help="Date à analyser.",
    )

    parser.add_argument(
        "--group-time",
        dest="group_time",
        default="30",
        type=int,
        help="Regroupage (en minutes). Par ex, utiliser '30' veut dire qu'une moyenne sera faite toutes les 30mn .",
    )

    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Afficher des informations pour aider au debugging.",
    )

    args = parser.parse_args()

    analyser = Analyser(
        host=args.server,
        sms_credentials=parse_credentials(args.credentials),
        max_temperature=args.max_temperature,
        dry_run=args.dry_run,
        verbose=args.verbose,
        send_test_message=args.send_test_message,
        phone_numbers=args.phone_numbers,
    )
    analyser.run(
        date=args.date,
        fermenters=args.fermenters.split(","),
        group_time=args.group_time,
    )


if __name__ == "__main__":
    main()
