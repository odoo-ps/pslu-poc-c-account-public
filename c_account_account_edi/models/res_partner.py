from odoo import models, fields

class ResPartner(models.Model):
    _inherit = "res.partner"

    klnemo = fields.Char()
