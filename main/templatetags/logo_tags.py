from django import template

register = template.Library()


@register.filter
def company_logo_url(domain):
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return None
