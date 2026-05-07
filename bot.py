import requests, re, json
url='https://www.amazon.it/Mercusys-MS105G-Sdoppiatore-Auto-Negotiation-Switching/dp/B07RK6CVS3?ref_=Oct_d_obs_d_460169031_1&pd_rd_w=IMEwD&content-id=amzn1.sym.4dca35fa-dfa3-4fcb-9ffe-1a5b1a54acf3&pf_rd_p=4dca35fa-dfa3-4fcb-9ffe-1a5b1a54acf3&pf_rd_r=N71XTS0CJ0PQ8ZCVNY5P&pd_rd_wg=IzlTW&pd_rd_r=634b7f50-acc5-47e1-9fc4-56832db1b2e1&pd_rd_i=B07RK6CVS3&th=1&tag=capofferte-21'
headers={'User-Agent':'Mozilla/5.0','Accept-Language':'it-IT,it;q=0.9'}
html=requests.get(url,headers=headers,timeout=20).text
patterns=[r'"price":"([^"]+)"', r'"priceToPay":"([^"]+)"', r'"offerPrice":"([^"]+)"', r'"displayPrice":"([^"]+)"', r'id="corePrice_feature_div".*?a-offscreen">([^<]+)<', r'id="priceblock_ourprice"[^>]*>([^<]+)<', r'id="priceblock_dealprice"[^>]*>([^<]+)<']
found=[]
for p in patterns:
    m=re.search(p, html, re.S)
    if m:
        found.append((p,m.group(1)))
found[:10], len(html)
