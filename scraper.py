"""Building an interface through pdfminer to directly create python objects rather than 
writing out everything to XML, only to have to read them in again"""
from pdfminer.pdfparser import PDFDocument, PDFParser
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter, process_pdf
from pdfminer.pdfdevice import PDFDevice, TagExtractor
from pdfminer.converter import XMLConverter, HTMLConverter, TextConverter, PDFConverter, PDFLayoutAnalyzer
from pdfminer.pdfdevice import PDFDevice, PDFTextDevice
from pdfminer.pdffont import PDFUnicodeNotDefined
from pdfminer.layout import LTContainer, LTPage, LTText, LTLine, LTRect, LTCurve
from pdfminer.layout import LTFigure, LTImage, LTChar, LTTextLine
from pdfminer.layout import LTTextBox, LTTextBoxVertical, LTTextGroup
from pdfminer.utils import apply_matrix_pt, mult_matrix


def bbox2str((x0,y0,x1,y1)):
    return '%.3f,%.3f,%.3f,%.3f' % (x0, y0, x1, y1)

def enc(x, codec='ascii'):
    """Encodes a string for SGML/XML/HTML"""
    x = x.replace('&','&amp;').replace('>','&gt;').replace('<','&lt;').replace('"','&quot;')
    return x.encode(codec, 'xmlcharrefreplace')



import scraperwiki
import urllib
import StringIO

# made from inlining functions out of 
# https://github.com/euske/pdfminer/tree/master/pdfminer

class LLTLine(LTCurve):
    def __init__(self, linewidth, p0, p1):
        LTCurve.__init__(self, linewidth, [p0, p1])
        return

class LLTRect(LTCurve):
    def __init__(self, linewidth, (x0,y0,x1,y1)):
        LTCurve.__init__(self, linewidth, [(x0,y0), (x1,y0), (x1,y1), (x0,y1)])
        return

class LPDFLayoutAnalyzer(PDFTextDevice):

    def __init__(self, rsrcmgr, pageno=1, laparams=None):
        PDFTextDevice.__init__(self, rsrcmgr)
        self.pageno = pageno
        self.laparams = laparams
        self._stack = []
        return

    def begin_page(self, page, ctm):
        (x0,y0,x1,y1) = page.mediabox
        (x0,y0) = apply_matrix_pt(ctm, (x0,y0))
        (x1,y1) = apply_matrix_pt(ctm, (x1,y1))
        mediabox = (0, 0, abs(x0-x1), abs(y0-y1))
        self.cur_item = LTPage(self.pageno, mediabox)
        return

    def end_page(self, page):
        assert not self._stack
        assert isinstance(self.cur_item, LTPage)
        if self.laparams is not None:
            self.cur_item.analyze(self.laparams)
        self.pageno += 1
        self.receive_layout(self.cur_item)
        return

    def begin_figure(self, name, bbox, matrix):
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        return

    def end_figure(self, _):
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure)
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return

    def render_image(self, name, stream):
        assert isinstance(self.cur_item, LTFigure)
        item = LTImage(name, stream,
                       (self.cur_item.x0, self.cur_item.y0,
                        self.cur_item.x1, self.cur_item.y1))
        self.cur_item.add(item)
        return

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        shape = ''.join(x[0] for x in path)
        if shape == 'ml':
            # horizontal/vertical line
            (_,x0,y0) = path[0]
            (_,x1,y1) = path[1]
            (x0,y0) = apply_matrix_pt(self.ctm, (x0,y0))
            (x1,y1) = apply_matrix_pt(self.ctm, (x1,y1))
            if x0 == x1 or y0 == y1:
                self.cur_item.add(LTLine(gstate.linewidth, (x0,y0), (x1,y1)))
                return
        if shape == 'mlllh':
            # rectangle
            (_,x0,y0) = path[0]
            (_,x1,y1) = path[1]
            (_,x2,y2) = path[2]
            (_,x3,y3) = path[3]
            (x0,y0) = apply_matrix_pt(self.ctm, (x0,y0))
            (x1,y1) = apply_matrix_pt(self.ctm, (x1,y1))
            (x2,y2) = apply_matrix_pt(self.ctm, (x2,y2))
            (x3,y3) = apply_matrix_pt(self.ctm, (x3,y3))
            if ((x0 == x1 and y1 == y2 and x2 == x3 and y3 == y0) or
                (y0 == y1 and x1 == x2 and y2 == y3 and x3 == x0)):
                self.cur_item.add(LLTRect(gstate.linewidth, (x0,y0,x2,y2)))
                print self.interpreter.scs, self.interpreter.ncs, gstate, stroke, fill, (x0,y0,x2,y2)
                return
        # other shapes
        pts = []
        for p in path:
            for i in xrange(1, len(p), 2):
                pts.append(apply_matrix_pt(self.ctm, (p[i], p[i+1])))
        self.cur_item.add(LTCurve(gstate.linewidth, pts))
        return

    def render_char(self, matrix, font, fontsize, scaling, rise, cid):
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, unicode), text
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(matrix, font, fontsize, scaling, rise, text, textwidth, textdisp)
        self.cur_item.add(item)
        return item.adv

    def handle_undefined_char(self, font, cid):
        if self.debug:
            print >>sys.stderr, 'undefined: %r, %r' % (font, cid)
        return '(cid:%d)' % cid

    def receive_layout(self, ltpage):
        return


class LPDFConverter(LPDFLayoutAnalyzer):
    def __init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1, laparams=None):
        LPDFLayoutAnalyzer.__init__(self, rsrcmgr, pageno=pageno, laparams=laparams)
        self.outfp = outfp
        self.codec = codec
        return



##  XMLConverter
##
class LXMLConverter(LPDFConverter):

    def __init__(self, rsrcmgr, outfp):
        LPDFConverter.__init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1, laparams=None)
        self.imagewriter = None
        self.write_header()
        self.reset()
        return

    def reset(self):
            # isinstance(item, LTChar)
            # item.fontname, item.bbox, item.size, item.get_text()
        self.ltchars = [ ]   
            # isinstance(item, LLTRect)
            # item.linewidth, item.bbox
        self.ltrect = [ ]   

    def workout(self):
        print "number of ltchars", len(self.ltchars)
        print "\n".join(["%.3f,%3f %.3f,%.3f" % (item.bbox[0], item.bbox[1], item.bbox[2]-item.bbox[0], item.bbox[3]-item.bbox[1])  for item in self.ltrect])

    def write_header(self):
        self.outfp.write('<?xml version="1.0" encoding="%s" ?>\n' % self.codec)
        self.outfp.write('<pages>\n')
        return

    def write_footer(self):
        1/0
        self.outfp.write('</pages>\n')
        return
    
    def write_text(self, text):
        self.outfp.write(enc(text, self.codec))
        return

    def show_group(self, item):
        if isinstance(item, LTTextBox):
            self.outfp.write('<textbox id="%d" bbox="%s" />\n' %
                             (item.index, bbox2str(item.bbox)))
        elif isinstance(item, LTTextGroup):
            self.outfp.write('<textgroup bbox="%s">\n' % bbox2str(item.bbox))
            for child in item:
                self.show_group(child)
            self.outfp.write('</textgroup>\n')
        return


    def render(self, item):
        if isinstance(item, LTPage):
            assert False

        elif isinstance(item, LTChar):
            self.ltchars.append(item)
        elif isinstance(item, LLTRect):
            self.ltrect.append(item)

        elif isinstance(item, LTLine):
            self.outfp.write('<line linewidth="%d" bbox="%s" />\n' %
                             (item.linewidth, bbox2str(item.bbox)))
        elif isinstance(item, LTCurve):
            self.outfp.write('<curve linewidth="%d" bbox="%s" pts="%s"/>\n' %
                             (item.linewidth, bbox2str(item.bbox), item.get_pts()))
        elif isinstance(item, LTFigure):   # recursive
            self.outfp.write('<figure name="%s" bbox="%s">\n' %
                             (item.name, bbox2str(item.bbox)))
            for child in item:
                self.render(child)
            self.outfp.write('</figure>\n')
        elif isinstance(item, LTTextLine): # recursive
            self.outfp.write('<textline bbox="%s">\n' % bbox2str(item.bbox))
            for child in item:
                self.render(child)
            self.outfp.write('</textline>\n')
        elif isinstance(item, LTTextBox):  # recursive
            wmode = ''
            if isinstance(item, LTTextBoxVertical):
                wmode = ' wmode="vertical"'
            self.outfp.write('<textbox id="%d" bbox="%s"%s>\n' %
                             (item.index, bbox2str(item.bbox), wmode))
            for child in item:
                self.render(child)
            self.outfp.write('</textbox>\n')
        elif isinstance(item, LTText):
            self.outfp.write('<text>%s</text>\n' % item.get_text())
        elif isinstance(item, LTImage):
            if self.imagewriter is not None:
                name = self.imagewriter.export_image(item)
                self.outfp.write('<image src="%s" width="%d" height="%d" />\n' %
                                 (enc(name), item.width, item.height))
            else:
                self.outfp.write('<image width="%d" height="%d" />\n' %
                                 (item.width, item.height))
        else:
            assert 0, item
        return

    def receive_layout(self, ltpage):
        self.outfp.write('<page id="%s" bbox="%s" rotate="%d">\n' %
                         (ltpage.pageid, bbox2str(ltpage.bbox), ltpage.rotate))
        for child in ltpage:
            self.render(child)
        if ltpage.groups is not None:
            self.outfp.write('<layout>\n')
            for group in ltpage.groups:
                show_group(group)
            self.outfp.write('</layout>\n')
        self.outfp.write('</page>\n')

        #render(ltpage)
        return

    def close(self):
        self.write_footer()
        return


def LPDFPageInterpreter(PDFPageInterpreter):
    def __init__(self, rsrcmgr, device):
        PDFPageInterpreter.__init__(self, rsrcmgr, device)

    def do_G(self, gray):
        #self.do_CS(LITERAL_DEVICE_GRAY)
        print "do_G"
    def do_g(self, gray):
        #self.do_cs(LITERAL_DEVICE_GRAY)
        print "do_g"
    def do_RG(self, r, g, b):
        #self.do_CS(LITERAL_DEVICE_RGB)
        print "do_RG", r, g, b
    def do_rg(self, r, g, b):
        #self.do_cs(LITERAL_DEVICE_RGB)
        print "do_rg", r, g, b



pdfin = StringIO.StringIO()
pdfin.write(urllib.urlopen("https://views.scraperwiki.com/run/frac_focus_dashboard_1/?pdfapi=37-115-20228").read())
rsrcmgr = PDFResourceManager()

sout = StringIO.StringIO()

device = LXMLConverter(rsrcmgr, sout)

parser = PDFParser(pdfin)
doc = PDFDocument()
parser.set_document(doc)
doc.set_parser(parser)
doc.initialize()

interpreter = LPDFPageInterpreter(rsrcmgr, device)
device.interpreter = interpreter
for page in doc.get_pages():
    device.reset()
    interpreter.process_page(page)
    device.workout()

print sout.getvalue()

