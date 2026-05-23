"""
Built-in signature templates for known organizations.

Templates are simplified for QTextEdit (which has limited HTML/CSS support
compared to a real browser): we use inline styles, no flexbox, simple
table layouts only when necessary.
"""

# Tunas Group / Tunas Rent corporate template.
# Image URLs point to the public icon repo used in the official Outlook
# signature, so they survive auto-embedding into base64 on first paste.
TUNAS_TEMPLATE = """\
<p style="margin:0;font-family:'Segoe UI',sans-serif;font-size:11pt;\
color:#1f497d;"><b>Best Regards,</b></p>
<p style="margin:0;">&nbsp;</p>
<table cellpadding="0" cellspacing="0" \
style="font-family:'Segoe UI',sans-serif;border-collapse:collapse;">
  <tr>
    <td style="padding:0 12px 4px 0;vertical-align:top;">
      <p style="margin:0;font-size:12pt;color:#1f497d;">\
<b>{name}</b></p>
      <p style="margin:0;font-size:10pt;color:#000;">{role}</p>
      <p style="margin:6px 0 0 0;font-size:10pt;color:#000;">\
Tel : {phone}</p>
      <p style="margin:0;font-size:10pt;color:#000;">\
Fax : 021-7486 5000</p>
    </td>
    <td style="padding:0 0 4px 12px;vertical-align:middle;\
border-left:1px solid #e1dfdd;">
      <img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/TUNASRENT-LOGO_fix.png" alt="Tunas Rent" \
width="268" style="max-width:268px;height:auto;display:block;">
    </td>
  </tr>
</table>
<p style="margin:8px 0 0 0;border-top:2px solid #230299;width:330px;">&nbsp;</p>
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
<img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/Icon-01.png" alt="Instagram" width="23" \
style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.facebook.com/tunasrent/">\
<img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/Icon-02.png" alt="Facebook" width="23" \
style="height:23px;border:0;"></a>
      &nbsp;
      <a href="https://www.youtube.com/channel/UC5PEDPtC8Ng4cL7jrWYqLBg">\
<img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/Icon-03.png" alt="YouTube" width="18" \
style="height:22px;border:0;"></a>
    </td>
  </tr>
</table>
<p style="margin:6px 0 0 0;font-family:'Segoe UI',sans-serif;">
  <img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/Icon-04.png" alt="Call 0800-1503-007" width="177" \
style="height:12px;border:0;vertical-align:middle;">
  &nbsp;&nbsp;
  <a href="https://wa.me/6281380909100">\
<img src="https://raw.githubusercontent.com/KhatarMalayki/icontunas/\
master/ICON/Icon-05.png" alt="WA 0813-8090-9100" width="182" \
style="height:13px;border:0;vertical-align:middle;"></a>
</p>
"""


def render_tunas(name: str, role: str = "IT Operational",
                 phone: str = "021-7486 1000",
                 email: str = "khatar@tunasgroup.com",
                 address: str = "Bintaro Komersial CBD B7 Kavling A1/02, "
                                "Bintaro Jaya, Tangerang 15224") -> str:
    """Render the Tunas template with user-specific fields filled in."""
    return TUNAS_TEMPLATE.format(
        name=name or "Your Name",
        role=role or "Your Role",
        phone=phone or "021-7486 1000",
        email=email or "user@tunasgroup.com",
        address=address or "Bintaro Komersial CBD B7 Kavling A1/02, "
                           "Bintaro Jaya, Tangerang 15224",
    )


TEMPLATES = {
    "tunas": {
        "label": "Tunas Group / Tunas Rent",
        "render": render_tunas,
    },
}
