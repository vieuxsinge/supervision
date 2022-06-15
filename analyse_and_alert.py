# coding=utf-8

import delegator

from influxdb import InfluxDBClient
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
                      888
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
        signal_group_id=None,
        signal_cli="/usr/bin/signal-cli",
        max_temperature=23,
        verbose=False,
        dry_run=False,
        send_test_message=False,
    ):
        self._client = InfluxDBClient(host, 8086)
        self.signal_group_id = signal_group_id
        self.verbose = verbose
        self.dry_run = dry_run
        self.max_temperature = max_temperature
        self.send_test_message = send_test_message
        self.signal_cli = signal_cli

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
            "debug": ("  " + "🐛", fg.rs),
            "end": ("\r\n", bg.rs),
        }
        before, after = icons.get(channel, "")
        print(f"{bg.black}{before} {message} {after}")

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
        command = f'{self.signal_cli} send -m "{message}" -g {self.signal_group_id}'
        resp = delegator.run(command)
        self.log("debug", command)
        if resp.err:
            self.log("error", resp.err)
        else:
            self.log("phone", f"Message de groupe envoyé à {self.signal_group_id}")


def parse_credentials(filename):
    text = pathlib.Path(filename).read_text("utf-8").strip("\n")
    return text.split(":")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyse influxdb data, looking for anomalies"
    )

    parser.add_argument(
        "--fermenters",
        dest="fermenters",
        help="Fermentation vessels to look for.",
        default="f1,f2,f3,f4",
    )

    parser.add_argument(
        "--server",
        dest="server",
        help="Server to connect to.",
        default="supervision.vieuxsinge.com",
    )

    parser.add_argument(
        "--max-temperature",
        dest="max_temperature",
        type=int,
        help="Max temperature allowed in the Fermentation vessels",
        default=200,
    )

    parser.add_argument(
        "--signal-cli",
        dest="signal_cli",
        help="Path to the signal-cli executable",
        default="/usr/bin/signal-cli-" #FIXME
    )

    parser.add_argument(
        "--signal-group-id",
        dest="signal_group_id",
        required=True,
        help='The signal group id to send the messages to'
    )

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Check for temperatures, but do not send messages.",
    )

    parser.add_argument(
        "--send-test-message",
        dest="send_test_message",
        action="store_true",
        default=False,
        help="Sends a test message to check everything works.",
    )

    parser.add_argument(
        "--date", dest="date", default="now", help="Date to look for.",
    )

    parser.add_argument(
        "--group-time",
        dest="group_time",
        default="120",
        type=int,
        help="Number of minutes to group the data in. For instance, using '30' means a mean will be done every 30mn .",
    )

    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=False,
        help="Be verbose, in order to help during debug",
    )

    args = parser.parse_args()

    analyser = Analyser(
        host=args.server,
        signal_group_id=args.signal_group_id,
        max_temperature=args.max_temperature,
        dry_run=args.dry_run,
        verbose=args.verbose,
        send_test_message=args.send_test_message,
        signal_cli=args.signal_cli
    )
    analyser.run(
        date=args.date,
        fermenters=args.fermenters.split(","),
        group_time=args.group_time,
    )


if __name__ == "__main__":
    main()
