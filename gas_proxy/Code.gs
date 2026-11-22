/**
 * USA Drop Radar — Google Apps Script US Proxy
 * Деплой: script.google.com -> Deploy -> New deployment -> Web app
 * Execute as: Me | Who has access: Anyone
 * Полученный URL вставь в .env на Oracle Cloud в переменную GAS_PROXY_URL
 */

function doGet(e) {
  var url = (e && e.parameter && e.parameter.url) ? String(e.parameter.url).trim() : "";
  if (!url) {
    return jsonResponse({ status: "error", message: "Missing url parameter" });
  }

  try {
    var result = scrapeProduct(url);
    return jsonResponse(result);
  } catch (err) {
    return jsonResponse({ status: "error", message: String(err) });
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function scrapeProduct(url) {
  var headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
  };

  var res = {
    status: "success",
    price: "NOT_FOUND",
    image: null,
    title: null,
    brand: null,
    sizes: [],
    in_stock: true,
    promo: null
  };

  var urlLower = url.toLowerCase();

  // 1. Adidas availability API directly from Google US IP
  if (urlLower.indexOf("adidas.") !== -1) {
    var skuMatch = url.match(/\/([A-Z0-9]{6})\.html/i);
    if (skuMatch) {
      try {
        var hostMatch = url.match(/^(https?:\/\/[^\/]+)/i);
        var apiUrl = (hostMatch ? hostMatch[1] : "https://www.adidas.com") + "/api/products/" + skuMatch[1].toUpperCase() + "/availability";
        var apiResp = UrlFetchApp.fetch(apiUrl, { muteHttpExceptions: true, headers: headers });
        if (apiResp.getResponseCode() === 200) {
          var apiData = JSON.parse(apiResp.getContentText());
          var vList = apiData.variation_list || [];
          var adSizes = [];
          for (var i = 0; i < vList.length; i++) {
            if (vList[i].availability_status === "IN_STOCK" && vList[i].size) {
              adSizes.push(String(vList[i].size).trim());
            }
          }
          res.sizes = dedupe(adSizes);
          res.in_stock = res.sizes.length > 0;
        }
      } catch (e) {}
    }
  }

  // 2. Fetch product HTML page from Google US IP
  var resp = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    followRedirects: true,
    headers: headers
  });

  if (resp.getResponseCode() !== 200) {
    return res;
  }

  var html = resp.getContentText();

  // Title
  res.title = extractMeta(html, "og:title");
  if (!res.title) {
    var tMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
    if (tMatch) res.title = decodeHtml(tMatch[1]).trim();
  }

  // Image
  res.image = extractMeta(html, "og:image:secure_url")
    || extractMeta(html, "og:image")
    || extractMeta(html, "twitter:image");
  if (!res.image) {
    var jsonImg = html.match(/"image"\s*:\s*"(https?:\\?\/\\?\/[^"\\]+)"/i)
      || html.match(/"image"\s*:\s*\[\s*"(https?:\\?\/\\?\/[^"\\]+)"/i);
    if (jsonImg) {
      res.image = jsonImg[1].replace(/\\\//g, "/");
    }
  }

  // Price
  var metaPrice = extractMeta(html, "product:price:amount") || extractMeta(html, "og:price:amount");
  var priceNum = null;
  if (metaPrice && /^\d+(?:[.,]\d{1,2})?$/.test(metaPrice)) {
    priceNum = metaPrice.replace(",", ".");
  } else {
    var pMatch = html.match(/"price"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?/i)
      || html.match(/itemprop=["']price["'][^>]+content=["'](\d+(?:\.\d{1,2})?)["']/i);
    if (pMatch) priceNum = pMatch[1];
  }
  if (priceNum) {
    var sym = detectCurrency(html, url);
    res.price = sym + priceNum;
  }

  // Sizes (if not already fetched via API)
  if (!res.sizes.length) {
    res.sizes = extractSizesFromHtml(html, urlLower);
  }

  // Out of stock check
  if (res.sizes.length > 0) {
    res.in_stock = true;
  } else if (/"availability"\s*:\s*"(?:https?:\/\/schema\.org\/)?OutOfStock"/i.test(html)
      && !/"availability"\s*:\s*"(?:https?:\/\/schema\.org\/)?InStock"/i.test(html)) {
    res.in_stock = false;
  }

  // Promo code on page
  res.promo = extractPagePromo(html);

  return res;
}

function extractMeta(html, propName) {
  var esc = propName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  var p1 = new RegExp('<meta[^>]+(?:property|name)=["\']' + esc + '["\'][^>]+content=["\']([^"\']+)["\']', "i");
  var p2 = new RegExp('<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + esc + '["\']', "i");
  var m = html.match(p1) || html.match(p2);
  return m ? decodeHtml(m[1]).trim() : null;
}

function detectCurrency(html, url) {
  var m = html.match(/"priceCurrency"\s*:\s*"([A-Z]{3})"/i);
  if (m) {
    var c = m[1].toUpperCase();
    if (c === "EUR") return "€";
    if (c === "GBP") return "£";
    if (c === "USD") return "$";
  }
  if (/\.co\.uk/i.test(url)) return "£";
  if (/\.(de|fr|es|it|eu|nl)\b/i.test(url)) return "€";
  return "$";
}

function extractSizesFromHtml(html, urlLower) {
  var found = [];

  // 1. JSON-LD hasVariant / offers
  var ldRegex = /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  var ldMatch;
  while ((ldMatch = ldRegex.exec(html)) !== null) {
    try {
      var obj = JSON.parse(ldMatch[1].trim());
      var nodes = Array.isArray(obj) ? obj : [obj];
      for (var n = 0; n < nodes.length; n++) {
        var node = nodes[n];
        if (!node || typeof node !== "object") continue;
        var variants = node.hasVariant || node.offers || [];
        if (!Array.isArray(variants)) variants = [variants];
        for (var v = 0; v < variants.length; v++) {
          var vr = variants[v];
          if (!vr || typeof vr !== "object") continue;
          var avail = String(vr.availability || (vr.offers && vr.offers.availability) || "").toLowerCase();
          if ((avail.indexOf("instock") !== -1 || avail === "") && vr.size) {
            found.push(String(vr.size).trim());
          }
        }
      }
    } catch (e) {}
  }
  if (found.length) return dedupe(found).slice(0, 30);

  // 2. Levi's variantOptions ("stockLevelStatus":"inStock" ... "size":"M" / "variantOptionQualifiers")
  if (urlLower.indexOf("levi.com") !== -1) {
    var leviRegex = /"stockLevelStatus"\s*:\s*"(?:inStock|lowStock)"[\s\S]{0,300}?"value"\s*:\s*"([A-Za-z0-9./+\s-]{1,12})"/gi;
    var lm;
    while ((lm = leviRegex.exec(html)) !== null) {
      if (isValidSizeToken(lm[1])) found.push(lm[1].trim());
    }
    if (found.length) return dedupe(found).slice(0, 30);
  }

  // 3. Generic inline JSON variants (Calvin Klein, Shopify, SFCC, etc.)
  var genRegex = /"(?:size|sizeName|option1|displayValue)"\s*:\s*"([A-Za-z0-9./+\s-]{1,18})"[^\}]{0,160}"(?:available|inStock|isAvailable|selectable|orderable)"\s*:\s*true/gi;
  var gm;
  while ((gm = genRegex.exec(html)) !== null) {
    if (isValidSizeToken(gm[1])) found.push(gm[1].trim());
  }

  return dedupe(found).slice(0, 30);
}

function isValidSizeToken(val) {
  return /^(?:XXS|XS|S|M|L|XL|XXL|2XL|3XL|4XL|\d{1,2}(?:\.\d)?|US\s*\d{1,2}(?:\.\d)?|EU\s*\d{2}(?:\.\d)?|\d{2}X\d{2})$/i.test(String(val).trim());
}

function extractPagePromo(html) {
  var visible = html
    .replace(/<(script|style|noscript)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ");
  var m = visible.match(/(?:extra\s+|take\s+|get\s+|save\s+)?(\d{1,2})\s*%\s*off.{0,70}?(?:use\s+|with\s+|w\/\s*)?(?:promo\s+|coupon\s+)?code[:\s"']+([A-Z0-9][A-Z0-9_-]{2,18})\b/i);
  if (m) {
    var pct = parseInt(m[1], 10);
    var code = m[2].toUpperCase();
    if (pct >= 5 && pct <= 80) {
      return { code: code, pct: pct, source: "GAS" };
    }
  }
  return null;
}

function dedupe(arr) {
  var seen = {};
  var out = [];
  for (var i = 0; i < arr.length; i++) {
    var k = String(arr[i]).trim();
    if (k && !seen[k.toLowerCase()]) {
      seen[k.toLowerCase()] = true;
      out.push(k);
    }
  }
  return out;
}

function decodeHtml(str) {
  return String(str)
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}
