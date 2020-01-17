# coding=utf-8

from influxdb import InfluxDBClient
import requests
import pathlib


def unpack(s):
    return ", ".join(map(str, s))


class Anomaly(Exception):
    def __init__(self, message, data):
        self.message = message
        self.data = data
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
    ):
        self._client = InfluxDBClient(host, 8086)
        self.sms_credentials = sms_credentials
        self.verbose = verbose
        self.dry_run = dry_run
        self.max_temperature = max_temperature

        if self.verbose:

            def _query(*args, **kwargs):
                print(kwargs["query"])
                return self._client._query(*args, **kwargs)

            self._client._query = self._client.query
            self._client.query = _query

    def log(self, channel, message):
        icons = {"info": "→", "error": "💥", "check": "🎉", "phone": "📱"}
        print(f"{icons.get(channel, '')} {message}")

    def run(self, fermenters, date="now"):
        self.log(
            "info", f"Recherche d'anomalies pour les fermenteurs {unpack(fermenters)}."
        )
        for fermenter in fermenters:
            try:
                self.analyse(fermenter, start_time=date, analysis_duration=15)
            except Anomaly as e:
                self.send_alert(e)
            else:
                self.log("check", f"Pas d'anomalies detectées pour {fermenter}.")

    def get_temperatures(self, fermenter, start_time="now", analysis_duration=15):
        group_time = round(analysis_duration / 3)

        if start_time == "now":
            start_time = "now()"

        query = f"""
        SELECT mean("value") FROM "autogen"."mqtt_consumer_float"
        WHERE ("topic" = 'fermenters/{fermenter}/temperature')
        AND time >=  {start_time} -72h
        GROUP BY time({group_time}m) fill(previous)
        """

        response = self._client.query(query=query, database="telegraf")
        if not response:
            raise Anomaly("no-temperatures", {"fermenter": fermenter})

        return [temp for _, temp in response.raw["series"][0]["values"] if temp]

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

    def analyse(self, fermenter, start_time="now", analysis_duration=15):
        """Analyses the data, trying to find problems. Alerts if during time_window,
        the temperature rises whereas it's supposed to be cooling.
        """
        all_temperatures = self.get_temperatures(
            fermenter, start_time, analysis_duration
        )

        # Do the computation on the last 6 values (= last 30mn)
        context = dict(
            fermenter=fermenter,
            temperatures=all_temperatures[-6:],
            is_cooling=self.get_cooling_info(fermenter, start_time),
            setpoint=self.get_setpoint(fermenter),
            max_temp=self.max_temperature,
        )
        self.log("info", context)

        self.check_temperature_convergence(**context)
        self.check_temperature_max(**context)

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
        data = anomaly.data
        anomaly_type = anomaly.message

        if anomaly_type == "temperature-rising":
            message = (
                f"""Attention, le fermenteur {data['fermenter']} grimpe en température """
                f"""({unpack([round(d, 2) for d in data['temperatures']])}), alors qu'il est sensé refroidir !"""
            )
        elif anomaly_type == "temperature-falling":
            message = (
                f"Attention, le fermenteur {data['fermenter']} descends en temperature "
                f"({unpack([round(d, 2) for d in data['temperatures']])}) alors qu'il est sensé monter."
            )
        elif anomaly_type == "no-temperatures":
            message = f"Aucune température n'est enregistrée par le fermenteur {data['fermenter']}"
        else:
            message = anomaly_type

        if self.dry_run:
            self.log("error", message)
        else:
            self.send_multiple_sms(message)

    def send_multiple_sms(self, message):
        for (user, password) in self.sms_credentials:
            response = requests.get(
                "https://smsapi.free-mobile.fr/sendmsg",
                params={"user": user, "pass": password, "msg": message},
            )
            self.log("phone", f"SMS envoyé : {message}")


def parse_credentials(filename):
    text = pathlib.Path(filename).read_text("utf-8")
    credentials = [line.split(":") for line in text.splitlines()]
    return credentials


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyse les données dans influxdb à la recherche d'anomalies"
    )

    parser.add_argument(
        "--fermenters",
        dest="fermenters",
        help="Fermenteurs à utiliser.",
        default="f1,f2,f3",
    )

    parser.add_argument(
        "--server",
        dest="server",
        help="Server to get the data from.",
        default="supervision.vieuxsinge.com",
    )

    parser.add_argument(
        "--max-temperature",
        dest="max_temperature",
        type=int,
        help="Température maximum autorisée dans les fermenteurs",
        default=25,
    )

    parser.add_argument(
        "-c",
        "--credentials",
        dest="credentials",
        default="credentials.txt",
        help="Path to the credentials filename.",
    )

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Ne pas envoyer les SMS.",
    )

    args = parser.parse_args()

    analyser = Analyser(
        host=args.server,
        sms_credentials=parse_credentials(args.credentials),
        max_temperature=args.max_temperature,
        dry_run=args.dry_run,
    )
    analyser.run(fermenters=args.fermenters.split(","))


if __name__ == "__main__":
    main()

