# IPC-2581 Parser for InteractiveHtmlBom project
# The goal of IPC-2581 (formerly ODB++) is to be a standard open format 
# for ECAD data exchange and replace the multi-file, multi-format packages 
# currently use to communicate a board design between engineering and 
# manufacturing
#
# Supporting IPC-2581 should allow wide compatibility with many commercial
# ECAD tools such as OrCAD, Mentor, and Altium because they all have
# commitment to support IPC-2581
#
# IPC-2581 examples (testcases) are freely available on the IPC website:
# http://www.ipc2581.com/a-test-cases/
#
# A draft rev C was available at: http://www.artwork.com/ipc2581/IPC-2581C.pdf
#
# IPC-2581 does not mandate what data is included so it is the up to
# the designer to configure the output with the board and BOM data
# needed for this parser. The parser should work when the following
# function modes are used: Full, Fabrication, Assembly, and Test
# See section 4.1 of the IPC-2581 spec for more details on modes
# 

import io
from xml.parsers.expat import ExpatError, errors
from xml.dom import minidom, Node
import math
import os.path
#from jsonschema import validate, ValidationError

from .common import EcadParser, Component, BoundingBox


class IPC2581Parser(EcadParser):
    COMPATIBLE_IPC_REV = 'C'
    IPC_rev = ''

    def extra_data_file_filter(self):
        return #"Json file ({f})|{f}".format(f=os.path.basename(self.file_name))

    def latest_extra_data(self, extra_dirs=None):
        return self.file_name

    def get_extra_field_data(self, file_name):
        if os.path.abspath(file_name) != os.path.abspath(self.file_name):
            return None

        _, components = self._parse()
        field_set = set()
        comp_dict = {}

        for c in components:
            ref_fields = comp_dict.setdefault(c.ref, {})

            for k, v in c.extra_fields.items():
                field_set.add(k)
                ref_fields[k] = v

        return list(field_set), comp_dict
    
    def get_Line_Widths(self, pcb):
        # Create a dictionary of line widths for later reference for both trace and silkscreen
        lines = {}
        for l in pcb.getElementsByTagName('EntryLineDesc'):
            lines.update({l.getAttribute('id'):l.getElementsByTagName('LineDesc')[0].getAttribute('lineWidth')})

        # if lines is empty, raise error
        return lines
    
    def get_Shapes(self, pcb):
        # IPC-2581 EntryStandard defines shapes that are used for component pads and
        # other repeating features like thieving. There are many shape definitions,
        # this parser will start with circle, rectcenter, and polygon
        # Assume all polygon shapes are filled, will need to enumerate DictionaryFillDesc 
        # if other types of fill are needed.
        # These are translated to circle, rect, and polygon drawings in a shapes dictionary
        # The dictionary format will use the Shape_ID : [Drawing_Type, drawing{}]
        # Footprint processing will reference this dictionary later
        shapes = {}
        for shape in pcb.getElementsByTagName('EntryStandard'):
            sn = shape.getAttribute('id')
            if shape.getElementsByTagName('Circle'):
                type = 'circle'
                size = shape.getElementsByTagName('Circle')[0].getAttribute('diameter')
                size = [size, size]
            elif shape.getElementsByTagName('RectCenter'):
            shapes[sn] = [type, size]

        return shapes

    def validate_IPC2581(self, pcb):
        # The root element must have a IPC-2581 as the name to be valid
        if pcb.firstChild.tagName != 'IPC-2581':
            self.logger.error('XML header does not specify IPC2581')
            return False

        # How to handle different revisions?
        IPC_rev = pcb.firstChild.getAttribute('revision')
        if IPC_rev > self.COMPATIBLE_IPC_REV:
            self.logger.error('incompatible IPC2581 revision')
            return False

        return True

    def get_Component_Val(self, comp):
        # default is attribute 'part' which might be CIS part number
        val = comp.getAttribute('part')
        # OrCAD passes CIS value field via 'NonstandardAttribute'
        for e in comp.getElementsByTagName('NonstandardAttribute'):
            if e.getAttribute('name')=='VALUE': val=e.getAttribute('value')
        
        # if val is empty, raise error
        return val
    
    def get_Metadata(self, pcb):
        # StepRef.name is the layout filename in OrCAD, also present in the element 'Step'
        t = pcb.getElementsByTagName('StepRef')[0].getAttribute('name')
        # need to figure out how IPC handles other metadata
        return t,'','',''
    
    def get_LayerNames(self, pcb):
        # Layer names are not always consistent so use the "side" attribute
        # and "layerFunction" to determine where the top and bottom copper,
        # assembly, and silkscreen layers are.
        # IPC-2581C definition has layerFunctions for many types of layers like
        # 'ASSEMBLY', 'BOARDFAB', 'SILKSCREEN', etc. But as with any standard
        # that aims to be flexible, implementation can vary and user influenced.
        # Examples show fab and silkscreen layers as DOCUMENT in OrCAD outputs
        # so more code or user input will be needed to decode layers. One option
        # on the command line is to list all DOCUMENT layers and ask user to
        # choose if standard functions are not found.
        TopCuRef = BotCuRef = TopSilkRef = BotSilkRef = TopAsmRef = BotAsmRef = None
        for Layers in pcb.getElementsByTagName('Layer'):
            ls = Layers.getAttribute('side')
            lf = Layers.getAttribute('layerFunction')
            if ls == 'TOP':
                if lf == 'DOCUMENT': TopSilkRef = Layers.getAttribute('name')
                elif lf == 'CONDUCTOR': TopCuRef = Layers.getAttribute('name')
                elif lf == 'ASSEMBLY': TopAsmRef = Layers.getAttribute('name')
            elif ls == 'BOTTOM':
                if lf == 'DOCUMENT': BotSilkRef = Layers.getAttribute('name')
                elif lf == 'CONDUCTOR': BotCuRef = Layers.getAttribute('name')
                elif lf == 'ASSEMBLY': BotAsmRef = Layers.getAttribute ('name')

        # Return all variables even if layer doesn't exist
        return TopCuRef, BotCuRef, TopSilkRef, BotSilkRef, TopAsmRef, BotAsmRef

    def convert_PolyStepCurve(self, startX, startY, ctrX, ctrY, endX, endY, cw):
        # Given 3 points and direction, generate radius, start angle, end angle
        dX1 = ctrX - startX
        dY1 = ctrY - startY
        dX2 = ctrX - endX
        dY2 = ctrY - endY

        if (dX1 == 0) or (dY1 == 0):
            # the radius is either dX1 or dY1
            if (dX1 == 0):
                radius = abs(dY1)
                if (dY1 > 0):
                    angle1 = 270
                else:
                    angle1 = 90
            else:
                radius = abs(dX1)
                if (dX1 > 0):
                    angle1 = 0
                else:
                    angle1 = 180
        else:
            radius = math.sqrt((abs(dX1)**2)+(abs(dY1)**2))
            angle1 = (math.atan(abs(dX1)/abs(dY1)))*(180/math.pi)
            if (dX1 > 0) and (dY1 > 0):
                startAngle = 270 + angle1
            elif (dX1 < 0) and (dY1 > 0):
                startAngle = 270 - angle1
            elif (dX1 < 0) and (dY1 < 0):
                startAngle = 90 + angle1
            else:
                startAngle = 90 - angle1
                
        if (dX2 == 0) or (dY2 == 0):
            # the radius is either dX2 or dY2
            if (dX2 == 0):
                radius = abs(dY2)
                if (dY2 > 0):
                    angle2 = 270
                else:
                    angle2 = 90
            else:
                radius = abs(dX2)
                if (dX2 > 0):
                    angle2 = 0
                else:
                    angle2 = 180
        else:
            angle2 = (math.atan(abs(dX2)/abs(dY2)))*(180/math.pi)       
            if (dX2 > 0) and (dY2 > 0):
                endAngle = 270 + angle2
            elif (dX2 < 0) and (dY2 > 0):
                endAngle = 270 - angle2
            elif (dX2 < 0) and (dY2 < 0):
                endAngle = 90 + angle2
            else:
                endAngle = 90 - angle2

        if (cw == 'true'):
            return [ radius, startAngle, endAngle ]
        else:
            return [ radius, endAngle, startAngle ]
    
    def process_PolyStep(self, n, width=0.1):
        # IPC-2581 PolyStep is a sequence of segments that builds
        # on top of the last, so the first is always a simple coordinate
        # return a list of drawing elements
        NewX = 0
        NewY = 0
        e = []
        # Process PolyStep
        for p in n.childNodes:
            if p.nodeType == Node.ELEMENT_NODE:
                LastX = NewX
                LastY = NewY
                pname = p.tagName
                # Want to use pattern match, but breaks compatibility below Python 3.10
                if pname == 'PolyBegin':
                    # polygon start point
                    NewX = float(p.getAttribute('x'))
                    NewY = float(p.getAttribute('y'))
                elif pname == 'PolyStepSegment':
                    # polygon draw line
                    NewX = float(p.getAttribute('x'))
                    NewY = float(p.getAttribute('y'))
                    e.append({'type':'segment',
                                'start': [LastX, LastY],
                                'end': [NewX, NewY],
                                'wdith': width})
                elif pname == 'PolyStepCurve':
                    # polygon draw arc
                    NewX = float(p.getAttribute('x'))
                    NewY = float(p.getAttribute('y'))
                    CtrX = float(p.getAttribute('centerX'))
                    CtrY = float(p.getAttribute('centerY'))
                    arc = self.convert_PolyStepCurve(LastX, LastY, CtrX, CtrY, NewX, NewY, p.getAttribute('clockwise'))
                    e.append({'type':'arc',
                                'width': width,
                                'start': [CtrX, CtrY],
                                'radius': arc[0],
                                'startangle': arc[1],
                                'endangle': arc[2]})
        
        return e
    
    def get_Board_Outline(self, pcb):
        # IPC-2581C defines the board outline in the Profile element
        # There should only be one Profile with a single Polygon
        # element and optionally multiple Cutout elements
        profile = pcb.getElementsByTagName('Profile')[0]
        outline = []

        outline_width = self.set_outlineWidth(pcb)
        # Expect only one 'Polygon' child element for outline
        # This code will process the first one found
        # IPC-2581 doesn't define line width for board outline
        poly = profile.getElementsByTagName('Polygon')[0]
        # Process Polygon then append returned list to outline
        for p in self.process_PolyStep(poly, outline_width):
            outline.append(p)

        # another type of element is Cutout, add it to the list
        for n in profile.getElementsByTagName('Cutout'):
            # Process each Cutout then append returned list to outline
            for p in self.process_PolyStep(n, outline_width):
                outline.append(p)

        return outline
    
    def get_LayerDrawings(self, pcb, layerName, lines, moreShapes=0):
        # LayerFeatures are the artwork elements, it has a rich set of
        # child elements that will take time to implement a fully compliant
        # parser to extract all relevant drawings
        # Easiest path is find all polylines to get text and traces
        # Assume we can ignore pads if they are covered by footprints
        # Polygons for pours and silkscreen shapes should be fine with
        # the exception that some polygons can have cutouts which seems
        # too complicated to decode initially
        drawings = []

        # Screen for empty layerName which means the layer doesn't exist
        # in the design file, return an empty list
        if (layerName != None):
            for Layers in pcb.getElementsByTagName('LayerFeature'):
                #ls = Layers.getAttribute('layerRef')
                if Layers.getAttribute('layerRef') == layerName:
                    # Found the right layer, extract polylines
                    for poly in Layers.getElementsByTagName('Polyline'):
                        # Find the line width
                        LineName = poly.getElementsByTagName('LineDescRef')[0].getAttribute('id')
                        LineWidth = lines[LineName]
                        # Process polyline and append to drawings
                        for p in self.process_PolyStep(poly, LineWidth):
                            drawings.append(p)

                    # if moreShapes, look for other elements



        return drawings
    
    def set_outlineWidth(self, pcb):
        # Since the Profile element does not require a line width
        # this will generate a default line width of the outline
        # based on the design units definition.
        # IPC-2581 only allows three kinds of units
        unit = pcb.getElementByTagName('CadHeader')[0].getAttribute('units')

        if unit == 'INCH':
            return 0.005
        elif unit == 'MILLIMETER':
            return 0.127
        elif unit == 'MICRON':
            return 127.0
        

    def _parse(self):
        # This parser uses minidom to crawl through the XML file
        # Initially tried ElementTree but found it couldn't handle all cases
        try:
            pcb = minidom.parse(self.file_name)
        except ExpatError as e:
            self.logger.error('File {f} does is not contain XML. {m}'
                              .format(f=self.file_name, m=errors.messages[e.code]))
            return None, None

        #Check the XML is IPC2581
        if not self.validate_IPC2581(pcb):
            #self.logger.error('XML header does not specify IPC2581')
            return None, None

        # Extract line width data from XML, IPC2581 consolidates into one dictionary
        lines_Dict = self.get_Line_Widths(pcb)

        # Extract padstack data from XML, IPC2581 consolidates into one dictionary
        shape_Dict = self.get_Shapes(pcb)
        # Extract layers names from XML
        TopCu, BotCu, TopSilk, BotSilk, TopASM, BotASM = self.get_LayerNames(pcb)

        # Extract metadata from XML
        title, revision, company, file_date = self.get_Metadata(pcb)

        # Extract edges from XML
        edges = self.get_Board_Outline(pcb)

        # TODO: Extract netlist using 'LogicalNet' elements

        pcbdata = {
            "edges_bbox": {},
            "edges": edges,
            "drawings": {
                "silkscreen": {
                    'F': self.get_LayerDrawings(pcb, TopSilk, lines_Dict),
                    'B': self.get_LayerDrawings(pcb, BotSilk, lines_Dict),
                },
                "fabrication": {
                    'F': self.get_LayerDrawings(pcb, TopASM, lines_Dict),
                    'B': self.get_LayerDrawings(pcb, BotASM, lines_Dict), 
                }
            },
            "footprints": self.get_Footprints(pcb, lines_Dict, shape_Dict),
            "metadata": {
                "title": title,
                "revision": revision,
                "company": company,
                "date": file_date,
            },
            "bom": {},
            "font_data": {}
        }
        
        if self.config.include_tracks:
            # parse copper layer for only traces
            pcbdata["tracks"] = {
                'F': self.get_LayerDrawings(pcb, TopCu, lines_Dict),
                'B': self.get_LayerDrawings(pcb, BotCu, lines_Dict),
            }
            # zones are not supported
            pcbdata["zones"] = {'F': [], 'B': []}

        
        components = []
        comp_xml = pcb.getElementsByTagName('Component')
        for c in comp_xml:
            attr = 'Normal'
            extra_fields = {}
            NSAttr = c.getElementsByTagName('NonstandardAttribute')
            if NSAttr:
                for n in NSAttr:
                    # Duplicate fields will be overwrite each other in a dictionary
                    extra_fields.update({n.getAttribute('name'):n.getAttribute('value')})
            component = Component(c.getAttribute('refDes'),
                                  self.get_Component_Val(c),
                                  c.getAttribute('packageRef'),
                                  c.getAttribute('layerRef'),
                                  attr,
                                  extra_fields)
            components.append(component)
        
        #components = [Component(**c) for c in pcb['components']]

        self.logger.info('Successfully parsed {}'.format(self.file_name))

        return pcbdata, components

    def parse(self):
        pcbdata, components = self._parse()

        # override board bounding box based on edges
        board_outline_bbox = BoundingBox()
        for drawing in pcbdata['edges']:
            self.add_drawing_bounding_box(drawing, board_outline_bbox)
        if board_outline_bbox.initialized():
            pcbdata['edges_bbox'] = board_outline_bbox.to_dict()

        return pcbdata, components