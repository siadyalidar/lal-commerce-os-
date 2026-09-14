import requests

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
})

page_url = "https://www.hepsiburada.com/angelsin-siyah-yuksel-bel-bikini-takim-renkli-detaylarla-38-beden-80-polyamid-20-likra-p-HBV00000ONEUV-yorumlari"
r1 = s.get(page_url)
print("Sayfa status:", r1.status_code)
print("Toplanan cookieler:", s.cookies.get_dict())
print("---")

api_url = "https://user-content-gw-hermes.hepsiburada.com/queryapi/v2/ApprovedUserContents"
params = {
    "sku": "HBV00000ONEUV",
    "from": 0,
    "size": 10,
    "includeSiblingVariantContents": "true",
    "includeSummary": "true",
}
s.headers.update({
    "Referer": page_url,
    "Origin": "https://www.hepsiburada.com",
    "Accept": "application/json",
})
r2 = s.get(api_url, params=params)
print("API status:", r2.status_code)
print(r2.text[:500])
