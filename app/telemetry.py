from .httpclient import HttpClient
import gc

def sendTelemetry(logdata):
        
        http_client = HttpClient()
        print(logdata)
        try:
            response = http_client.get(f"https://homeservice.azurewebsites.net/api/msensreport?code=yTlDbj8p-zPx7IbnlLFEp4t9IuEvm-YblJBRPkDuO3ZMAzFufukojg==&log={logdata}")
            gc.collect()
        except Exception as e:
            print(f"Msg faile. Reason: {e}")
            gc.collect()
        return