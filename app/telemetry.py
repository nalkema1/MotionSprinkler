from .httpclient import HttpClient
import gc
import machine
import ubinascii
import keys

# Determine device unique MAC address
mac = ubinascii.hexlify(machine.unique_id(),':').decode()

encode_chars = {   " " :   "%20",
        "“":	"%22",
        "<":	"%3C",
        ">":	"%3E",
        "#":	"%23",
        "%":	"%25",
        "{":	"%7B",
        "}":    "%7D",
        "|":	"%7C",
        "\\":	"%5C",
        "^":	"%5E",
        "~":	"%7E",
        "[":	"%5B",
        "]":	"%5D"
    }

def sendTelemetry(logdata):

        if logdata is None:
            return

        if logdata == "None":    
            return

        print(f"logdata : {logdata}")

        headers = {"accept" : "text/html",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36"}
        http_client = HttpClient(headers=headers)

        logdata = mac + " : " + logdata
        logdata = urlencode(logdata)
        try:
            url = f"https://motionsprinkler.azurewebsites.net/api/msensreport?code={keys.appcode}&log={logdata}"
            print(f"URL: {url}")
            response = http_client.get(url)
            gc.collect()
            if response.status_code != 200:
                print(f"Msg failed. Status Code: {response.status_code} - URL: {url}")
        except Exception as e:
            print(f"Msg failed. Reason: {e}  - URL: {url}")
            gc.collect()
        return

def urlencode(string):

    encoded_string = ""
    for char in string:
        newchar = encode_chars.get(char)
        if newchar:
            encoded_string += newchar
        else:
            encoded_string += char

    return(encoded_string)
