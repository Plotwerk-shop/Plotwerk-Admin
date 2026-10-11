"""Fixed, owner-authenticated catalog/marketing operations. No arbitrary GraphQL."""
import base64
import io
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def bounded(value, maximum=255):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('Ungültiger Text.')
    return value


def identifier(value, kind):
    if not isinstance(value, str) or not re.fullmatch(r'gid://shopify/' + kind + r'/[0-9]+', value):
        raise ValueError('Ungültige ID.')
    return value


def amount(value, percent=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > (100 if percent else 99999999) or result.as_tuple().exponent < -2:
            raise ValueError('Ungültiger Betrag.')
        return result
    except InvalidOperation:
        raise ValueError('Ungültiger Betrag.') from None


def catalog_fields(body):
    title = bounded(body.get('title', '')).strip()
    if not title:
        raise ValueError('Titel fehlt.')
    out = {'title': title}
    for key, limit in [('descriptionHtml', 10000), ('handle', 255)]:
        if key in body:
            out[key] = bounded(body[key], limit)
    if 'seoTitle' in body or 'seoDescription' in body:
        out['seo'] = {'title': bounded(body.get('seoTitle', ''), 255), 'description': bounded(body.get('seoDescription', ''), 1000)}
    return out


class Features:
    def __init__(self, app):
        self.app = app

    def dispatch(self, action, b):
        api = self.app.api
        if action == 'product.detail':
            return api.graphql('''query($id:ID!,$after:String) { product(id:$id) { id title descriptionHtml handle status vendor productType tags seo { title description }
              variants(first:30,after:$after) { pageInfo { hasNextPage endCursor } nodes { id title price compareAtPrice sku barcode taxable inventoryPolicy inventoryItem { id tracked } } } } }''', {'id': identifier(b.get('id'), 'Product'), 'after': b.get('after')})
        if action in ('product.create', 'product.edit'):
            value = catalog_fields(b)
            status = b.get('status', 'DRAFT')
            if status not in ('ACTIVE', 'DRAFT', 'ARCHIVED'):
                raise ValueError('Ungültiger Status.')
            value.update(status=status, vendor=bounded(b.get('vendor', '')), productType=bounded(b.get('productType', '')))
            if 'tags' in b:
                value['tags'] = [bounded(tag.strip()) for tag in bounded(b['tags'], 2000).split(',') if tag.strip()]
            if action.endswith('edit'):
                value['id'] = identifier(b.get('id'), 'Product')
                return api.graphql('mutation($product:ProductUpdateInput!) { productUpdate(product:$product) { product { id } userErrors { message } } }', {'product': value})
            return api.graphql('mutation($product:ProductCreateInput!) { productCreate(product:$product) { product { id } userErrors { message } } }', {'product': value})
        if action == 'product.media':
            return api.graphql('query($id:ID!,$after:String) { product(id:$id) { media(first:25,after:$after) { pageInfo { hasNextPage endCursor } nodes { id alt status mediaContentType ... on MediaImage { image { url } } } } } }', {'id':identifier(b.get('id'),'Product'),'after':b.get('after')})
        if action == 'product.media.add':
            url=bounded(b.get('url',''),2048)
            if not url.startswith('https://'):
                raise ValueError('HTTPS-Bildadresse erforderlich.')
            return api.graphql('mutation($product:ProductUpdateInput!,$media:[CreateMediaInput!]) { productUpdate(product:$product,media:$media) { product { id } userErrors { message } } }', {'product':{'id':identifier(b.get('id'),'Product')},'media':[{'originalSource':url,'mediaContentType':'IMAGE','alt':bounded(b.get('alt',''),500)}]})
        if action in ('product.media.edit','product.media.remove'):
            value={'id':identifier(b.get('mediaId'),'MediaImage')}
            if action.endswith('remove'):
                value['referencesToRemove']=[identifier(b.get('id'),'Product')]
            else:
                value['alt']=bounded(b.get('alt',''),500)
                if b.get('url'):
                    url=bounded(b['url'],2048)
                    if not url.startswith('https://'):
                        raise ValueError('HTTPS-Bildadresse erforderlich.')
                    value['originalSource']=url
            return api.graphql('mutation($files:[FileUpdateInput!]!) { fileUpdate(files:$files) { files { id } userErrors { message } } }', {'files':[value]})
        if action == 'variant.edit':
            value = {'id': identifier(b.get('id'), 'ProductVariant'), 'price': str(amount(b.get('price'))), 'barcode': bounded(b.get('barcode', '')), 'inventoryItem': {'sku': bounded(b.get('sku', ''))}}
            value['compareAtPrice'] = str(amount(b['compareAtPrice'])) if b.get('compareAtPrice') else None
            if type(b.get('taxable')) is not bool or b.get('inventoryPolicy') not in ('DENY', 'CONTINUE'):
                raise ValueError('Ungültige Varianteneinstellung.')
            value.update(taxable=b['taxable'], inventoryPolicy=b['inventoryPolicy'])
            return api.graphql('mutation($productId:ID!,$variants:[ProductVariantsBulkInput!]!) { productVariantsBulkUpdate(productId:$productId,variants:$variants) { productVariants { id } userErrors { message } } }', {'productId': identifier(b.get('productId'), 'Product'), 'variants': [value]})
        if action == 'collection.detail':
            return api.graphql('query($id:ID!) { collection(id:$id) { id title handle descriptionHtml seo { title description } ruleSet { appliedDisjunctively rules { column relation condition } } image { url altText } } }', {'id': identifier(b.get('id'), 'Collection')})
        if action in ('collection.create', 'collection.edit'):
            value = catalog_fields(b)
            if b.get('imageUrl'):
                url = bounded(b['imageUrl'], 2048)
                if not url.startswith('https://'):
                    raise ValueError('HTTPS-Bildadresse erforderlich.')
                value['image'] = {'src': url, 'altText': bounded(b.get('imageAlt', ''))}
            if action.endswith('edit'):
                value['id'] = identifier(b.get('id'), 'Collection')
                return api.graphql('mutation($collection:CollectionUpdateInput!) { collectionUpdate(collection:$collection) { collection { id } userErrors { message } } }', {'collection': value})
            # Manual collection creation; automated sources are deliberately not rewritten.
            return api.graphql('mutation($input:CollectionInput!) { collectionCreate(input:$input) { collection { id } userErrors { message } } }', {'input': value})
        if action in ('collection.add', 'collection.remove'):
            ids = b.get('productIds')
            if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
                raise ValueError('Produkte fehlen.')
            values = {'id': identifier(b.get('id'), 'Collection'), 'products': [identifier(x, 'Product') for x in ids]}
            if action.endswith('add'):
                return api.graphql('mutation($id:ID!,$products:[ID!]!) { collectionAddProducts(id:$id,productIds:$products) { collection { id } userErrors { message } } }', values)
            return api.graphql('mutation($id:ID!,$products:[ID!]!) { collectionRemoveProducts(id:$id,productIds:$products) { job { id } userErrors { message } } }', values)
        if action == 'discounts':
            return api.graphql('''query($after:String) { discountNodes(first:25,after:$after) { pageInfo { hasNextPage endCursor } nodes { id discount { __typename ... on DiscountCodeBasic { title status startsAt endsAt codes(first:1) { nodes { code } } } ... on DiscountAutomaticBasic { title status startsAt endsAt } } } } }''', {'after': b.get('after')})
        if action == 'discount.create':
            value = {'title': bounded(b.get('title', '')).strip(), 'startsAt': self.date(b.get('startsAt')),
                     'customerGets': {'items': {'all': True}}, 'combinesWith': {'orderDiscounts': False, 'productDiscounts': False, 'shippingDiscounts': False}}
            if not value['title']:
                raise ValueError('Titel fehlt.')
            if b.get('endsAt'):
                value['endsAt'] = self.date(b['endsAt'])
                if value['endsAt'] <= value['startsAt']:
                    raise ValueError('Ende muss nach Beginn liegen.')
            discount = amount(b.get('value'), b.get('kind') == 'percent')
            if discount <= 0:
                raise ValueError('Rabatt muss größer null sein.')
            if b.get('kind') == 'percent':
                value['customerGets']['value'] = {'percentage': float(discount / 100)}
            elif b.get('kind') == 'fixed':
                value['customerGets']['value'] = {'discountAmount': {'amount': str(discount), 'appliesOnEachItem': False}}
            else:
                raise ValueError('Ungültiger Rabatttyp.')
            quantity = b.get('minimumQuantity', 1)
            if type(quantity) is not int or not 1 <= quantity <= 1000000:
                raise ValueError('Ungültige Mindestmenge.')
            value['minimumRequirement'] = {'quantity': {'greaterThanOrEqualToQuantity': str(quantity)}}
            if b.get('mode') == 'code':
                value['code'] = bounded(b.get('code', ''), 100).strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{3,100}', value['code']):
                    raise ValueError('Ungültiger Rabattcode.')
                value['context'] = {'all': True}
                value['appliesOncePerCustomer'] = b.get('once') is True
                if b.get('usageLimit') is not None:
                    limit = b['usageLimit']
                    if type(limit) is not int or not 1 <= limit <= 10000000:
                        raise ValueError('Ungültiges Nutzungslimit.')
                    value['usageLimit'] = limit
                return api.graphql('mutation($input:DiscountCodeBasicInput!) { discountCodeBasicCreate(basicCodeDiscount:$input) { codeDiscountNode { id } userErrors { message } } }', {'input': value})
            if b.get('mode') != 'automatic':
                raise ValueError('Ungültige Rabattart.')
            return api.graphql('mutation($input:DiscountAutomaticBasicInput!) { discountAutomaticBasicCreate(automaticBasicDiscount:$input) { automaticDiscountNode { id } userErrors { message } } }', {'input': value})
        if action == 'ticker':
            return api.graphql('query { shop { id metafield(namespace:"plotwerk",key:"news_ticker") { value compareDigest } } }')
        if action == 'ticker.save':
            data = {'enabled': b.get('enabled') is True, 'text': bounded(b.get('text', ''), 500), 'link': bounded(b.get('link', ''), 2048)}
            if data['link'] and (not data['link'].startswith('/') or data['link'].startswith('//') or '\\' in data['link'] or re.search(r'[\x00-\x20]', data['link'])):
                raise ValueError('Nur interne Shop-Links erlaubt.')
            shop = api.graphql('query { shop { id } }')['shop']['id']
            return api.graphql('mutation($fields:[MetafieldsSetInput!]!) { metafieldsSet(metafields:$fields) { metafields { id } userErrors { message } } }', {'fields': [{'ownerId': shop, 'namespace': 'plotwerk', 'key': 'news_ticker', 'type': 'json', 'value': json.dumps(data), 'compareDigest': b.get('compareDigest')}]})
        if action == 'label':
            import barcode
            from barcode.writer import SVGWriter
            data = bounded(b.get('code', ''), 80)
            if not re.fullmatch(r'[\x20-\x7e]{1,80}', data):
                raise ValueError('Code muss 1–80 druckbare ASCII-Zeichen enthalten.')
            output = io.BytesIO()
            barcode.get('code128', data, writer=SVGWriter()).write(output, {'module_height': 12, 'font_size': 9, 'quiet_zone': 4})
            return {'svg': base64.b64encode(output.getvalue()).decode()}
        return None

    @staticmethod
    def date(value):
        if value is None:
            return datetime.now(timezone.utc).isoformat()
        dt = datetime.fromisoformat(bounded(value, 50).replace('Z', '+00:00'))
        if not dt.tzinfo:
            raise ValueError('Zeitzone fehlt.')
        return dt.astimezone(timezone.utc).isoformat()
