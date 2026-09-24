"""
Built-in signature templates for known organizations.

Templates are simplified for QTextEdit (which has limited HTML/CSS support
compared to a real browser): we use inline styles, no flexbox, simple
table layouts only when necessary.
"""
from core._tunas_icons import ICONS as _TUNAS_ICONS
from core._tunas_logistic_logo import LOGO_DATA_URI as _TUNAS_LOGISTIC_LOGO

# Tunas Group / Tunas Rent corporate template.
#
# Images are embedded as base64 data: URIs (see core/_tunas_icons.py, generated
# by _gen_tunas_icons.py). Previously these pointed to raw.githubusercontent.com
# and were downloaded into base64 on first insert — but on machines where a
# corporate proxy/firewall blocked GitHub (or the user was offline), the
# download failed and the icons never rendered. Bundling them inline makes the
# template fully self-contained: it renders offline and behind any firewall,
# and reaches recipients intact (the send path turns these data: URIs into
# proper Content-ID parts).
TUNAS_TEMPLATE = """\
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:11pt;\
color:#1f497d;"><b>Best Regards,</b></p>
<p style="margin:0;">&nbsp;</p>
<table cellpadding="0" cellspacing="0" \
style="font-family:'Segoe UI',sans-serif;border-collapse:collapse;">
  <tr>
    <td style="padding:0 12px 4px 0;vertical-align:top;">
      <p style="margin:0;font-size:12pt;color:#1f497d;">\
<b>{name}</b> &nbsp;|&nbsp; <span style="font-size:10pt;color:#000;\
font-weight:normal;">{role}</span></p>
      <p style="margin:6px 0 0 0;font-size:10pt;color:#000;">\
Tel : {phone}</p>
      <p style="margin:0;font-size:10pt;color:#000;">\
Fax : 021-7486 5000</p>
    </td>
    <td style="padding:0 0 4px 12px;vertical-align:middle;">
      <img src="{logo}" alt="Tunas Rent" \
width="268" style="max-width:268px;height:auto;display:block;">
    </td>
  </tr>
</table>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:8px 0 0 0;">
  <tr>
    <td width="330" style="border-top:2px solid #230299;font-size:0;line-height:0;">&nbsp;</td>
  </tr>
</table>
<table cellpadding="0" cellspacing="0" \
style="font-family:'Segoe UI',sans-serif;border-collapse:collapse;\
margin-top:6px;">
  <tr>
    <td style="padding:0 12px 0 0;vertical-align:top;">
      <p style="margin:0;font-size:9pt;color:#808080;">{address}</p>
      <p style="margin:0;font-size:9pt;color:#808080;">\
<a href="mailto:{email}" style="color:#808080;">{email}</a> &nbsp;|&nbsp; \
<a href="http://www.tunasrent.com/" style="color:#808080;">www.tunasrent.com</a></p>
    </td>
    <td style="padding:0 0 0 12px;vertical-align:middle;text-align:center;">
      <a href="https://www.instagram.com/tunas.rent/">\
<img src="{instagram}" alt="Instagram" width="23" \
style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.facebook.com/tunasrent/">\
<img src="{facebook}" alt="Facebook" width="23" \
style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.youtube.com/channel/UC5PEDPtC8Ng4cL7jrWYqLBg">\
<img src="{youtube}" alt="YouTube" width="18" \
style="height:22px;border:0;"></a>
    </td>
  </tr>
</table>
<p style="margin:6px 0 0 0;font-family:'Segoe UI',sans-serif;">
  <img src="{call}" alt="Call 0800-1503-007" width="177" \
style="height:12px;border:0;vertical-align:middle;">
  &nbsp;&nbsp;
  <a href="https://wa.me/6281380909100">\
<img src="{whatsapp}" alt="WA 0813-8090-9100" width="182" \
style="height:13px;border:0;vertical-align:middle;"></a>
</p>
"""


def render_tunas(name: str, role: str="IT Operational",
                 phone: str="021-7486 1000",
                 email: str="khatar@tunasgroup.com",
                 address: str="Bintaro Komersial CBD B7 Kavling A1/02, "
                                "Bintaro Jaya, Tangerang 15224") -> str:
    """Render the Tunas template with user-specific fields filled in.

    Icons are embedded inline (base64) so the signature renders without any
    network access — no dependency on raw.githubusercontent.com.
    """
    return TUNAS_TEMPLATE.format(
        name=name or "Your Name",
        role=role or "Your Role",
        phone=phone or "021-7486 1000",
        email=email or "user@tunasgroup.com",
        address=address or "Bintaro Komersial CBD B7 Kavling A1/02, "
                           "Bintaro Jaya, Tangerang 15224",
        logo=_TUNAS_ICONS["LOGO"],
        instagram=_TUNAS_ICONS["INSTAGRAM"],
        facebook=_TUNAS_ICONS["FACEBOOK"],
        youtube=_TUNAS_ICONS["YOUTUBE"],
        call=_TUNAS_ICONS["CALL"],
        whatsapp=_TUNAS_ICONS["WHATSAPP"],
    )


TUNAS_LOGISTIC_TEMPLATE = """\
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:11pt;color:#1f497d;"><b>Best Regards,</b></p>
<p style="margin:0;">&nbsp;</p>
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:12pt;color:#1f497d;"><b>{name}</b> &nbsp;|&nbsp; <span style="font-size:10pt;font-weight:normal;">{role}</span></p>
<p style="margin:0;">&nbsp;</p>
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:10pt;color:#000;">Tel : {phone}</p>
<p style="margin:8px 0 0 0;font-family:'Segoe UI',sans-serif;font-size:10pt;color:#000;">Fax : 021-7486 5000</p>
<p style="margin:0;">&nbsp;</p>
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:9pt;color:#808080;">{address}</p>
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:9pt;color:#808080;"><a href="mailto:{email}" style="color:#1f497d;">{email}</a></p>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:8px;font-family:'Segoe UI',sans-serif;">
  <tr>
    <td style="padding:0 18px 0 0;vertical-align:middle;">
      <img src="{call}" alt="Call 0800-1503-007" width="177" style="height:12px;border:0;vertical-align:middle;">
      &nbsp;
      <a href="https://wa.me/6281380909100"><img src="{whatsapp}" alt="WA 0813-8090-9100" width="182" style="height:13px;border:0;vertical-align:middle;"></a>
    </td>
    <td style="padding:0;vertical-align:middle;white-space:nowrap;">
      <a href="https://www.instagram.com/tunas.rent/"><img src="{instagram}" alt="Instagram" width="23" style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.facebook.com/tunasrent/"><img src="{facebook}" alt="Facebook" width="23" style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.youtube.com/channel/UC5PEDPtC8Ng4cL7jrWYqLBg"><img src="{youtube}" alt="YouTube" width="18" style="height:22px;border:0;"></a>
    </td>
  </tr>
</table>
<p style="margin:12px 0 0 0;font-family:'Segoe UI',sans-serif;">
  <img src="{logo}" alt="Tunas Logistic" width="202" style="width:202px;height:57px;border:0;display:block;">
</p>
"""


def render_tunas_logistic(
    name: str,
    role: str = "Finance & Billing Staff",
    phone: str = "021-7486 1000",
    email: str = "administration.staff@mitraanantamegah.com",
    address: str = (
        "Bintaro Komersial CBD B7 Kavling A1/02, "
        "Bintaro Jaya, Tangerang 15224"
    ),
) -> str:
    """Render the self-contained Tunas Logistic signature template."""
    return TUNAS_LOGISTIC_TEMPLATE.format(
        name=name or "Your Name",
        role=role or "Your Role",
        phone=phone or "021-7486 1000",
        email=email or "user@mitraanantamegah.com",
        address=address or (
            "Bintaro Komersial CBD B7 Kavling A1/02, "
            "Bintaro Jaya, Tangerang 15224"
        ),
        logo=_TUNAS_LOGISTIC_LOGO,
        instagram=_TUNAS_ICONS["INSTAGRAM"],
        facebook=_TUNAS_ICONS["FACEBOOK"],
        youtube=_TUNAS_ICONS["YOUTUBE"],
        call=_TUNAS_ICONS["CALL"],
        whatsapp=_TUNAS_ICONS["WHATSAPP"],
    )


TEMPLATES = {
    "tunas": {
        "label": "Tunas Group / Tunas Rent",
        "render": render_tunas,
    },
    "tunas_logistic": {
        "label": "Tunas Logistic / PT Mitra Ananta Megah",
        "render": render_tunas_logistic,
    },
}
