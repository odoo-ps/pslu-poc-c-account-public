from lxml import etree
from odoo.exceptions import UserError
from odoo import _, models

class XmlUtils(models.Model):
    _name = "xml.utils"

    def load_all_xmls(self, data):
        xml_docs = []
        errors = []

        for d in data:
            path = d.get('file')
            try:
                xml_docs.append(self.load_xml(path))
            except Exception as e:
                errors.append(_("File '%(path)s': %(error)s", path=path, error=str(e)))

        if errors:
            raise UserError(
                _("Error while loading XML files:\n\n") +
                "\n".join(errors)
            )

        return xml_docs

    def load_xml(self, file_path):
        if not file_path:
            raise UserError(_("Missing XML file path."))

        try:
            with open(file_path, 'rb') as file:
                content = file.read()
        except FileNotFoundError:
            raise UserError(_("File not found: %(path)s", path=file_path))
        except PermissionError:
            raise UserError(_("Permission denied when reading: %(path)s", path=file_path))
        except Exception as e:
            raise UserError(
                _("Unexpected error when reading file '%(path)s': %(error)s", path=file_path, error=str(e))
            )

        try:
            return etree.fromstring(content)
        except etree.XMLSyntaxError as e:
            raise UserError(_("Invalid XML in '%(path)s': %(error)s", path=file_path, error=str(e)))

    def xml_to_dict(self, element):
        def _convert(elem):
            node = {}

            # Attributes
            for key, value in elem.attrib.items():
                node[f"@{key}"] = value

            # Children
            children = list(elem)
            if children:
                children_dict = {}
                for child in children:
                    tag = etree.QName(child).localname
                    child_dict = _convert(child)

                    if tag in children_dict:
                        if not isinstance(children_dict[tag], list):
                            children_dict[tag] = [children_dict[tag]]
                        children_dict[tag].append(child_dict)
                    else:
                        children_dict[tag] = child_dict

                node.update(children_dict)

            # Text content
            text = (elem.text or "").strip()
            if text:
                if node:
                    node["#text"] = text
                else:
                    return text

            return node or None

        return {etree.QName(element).localname: _convert(element)}

    def load_datas(self, data):
        xml_list = self.load_all_xmls(data)
        return [self.xml_to_dict(xml) for xml in xml_list]
