# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

#This implementation of the gtkcodebuffer is an heavily modified fork
#The original one can be found at http://code.google.com/gtkcodebuffer

import gtk
import pango
import re
import sys
import os.path
import xml.sax
import imp
from xml.sax.handler import ContentHandler
from xml.sax.saxutils import unescape
import threading
import gobject
import copy
import time
from pge_defering import _DeferClass
import hildon

# defined the default styles
DEFAULT_STYLES = {
    'DEFAULT':      {'foreground': '#000000','background': '#FFFFFF'},
    'comment':      {'foreground': '#0000FF'},
    'preprocessor': {'foreground': '#A020F0'},
    'keyword':      {'foreground': '#A52A2A',
                     'weight': 'bold'},
    'special':      {'foreground': '#A52A2A'},
    'mark1':        {'foreground': '#008B8B'},
    'mark2':        {'foreground': '#6A5ACD'},
    'string':       {'foreground': '#FF00FF'},
    'number':       {'foreground': '#FF00FF'},
    'datatype':     {'foreground': '#2E8B57',
                     'weight': 'bold'},
    'function':     {'foreground': '#008A8C'},

    'link':         {'foreground': '#0000FF',
                     'underline': 'single'},
    'search_hilight': {'background': 'yellow'}}


def _main_is_frozen():
    """ Internal used function. """
    return (hasattr(sys, "frozen") or # new py2exe
            hasattr(sys, "importers") # old py2exe
            or imp.is_frozen("__main__")) # tools/freeze

if _main_is_frozen():
    this_module_path = os.path.dirname(sys.executable)
else:
    this_module_path = os.path.abspath(os.path.dirname(__file__))

# defines default-search paths for syntax-files
SYNTAX_PATH = [ os.path.join('.', 'syntax'),
                this_module_path,
                os.path.join(os.path.expanduser('~'),".pygtkeditor"),
                os.path.join(sys.prefix,"share","pygtkeditor","syntax"),
                '/opt/pygtkeditor/syntax']

# enable/disable debug-messages
DEBUG_FLAG  = False

#
# Some log functions...
#   (internal used)
def _log_debug(msg):
    if not DEBUG_FLAG:
        return
    sys.stderr.write("DEBUG: ")
    sys.stderr.write(msg)
    sys.stderr.write("\n")

def _log_warn(msg):
    sys.stderr.write("WARN: ")
    sys.stderr.write(msg)
    sys.stderr.write("\n")

def _log_error(msg):
    sys.stderr.write("ERROR: ")
    sys.stderr.write(msg)
    sys.stderr.write("\n")

class UndoableInsert(object):
    """something that has been inserted into our textbuffer"""
    def __init__(self, text_iter, text, length):
        self.offset = text_iter.get_offset()
        self.text = text
        self.length = length
        if self.length > 1 or self.text in ("\r", "\n", " "):
            self.mergeable = False
        else:
            self.mergeable = True

class UndoableDelete(object):
    """something that has ben deleted from our textbuffer"""
    def __init__(self, text_buffer, start_iter, end_iter):
        self.deleted_text = text_buffer.get_text(start_iter, end_iter)
        _log_debug('something deleted in the buffer'+self.deleted_text)
        self.start = start_iter.get_offset()
        self.end = end_iter.get_offset()
        # need to find out if backspace or delete key has been used
        # so we don't mess up during redo
        insert_iter = text_buffer.get_iter_at_mark(text_buffer.get_insert())
        if insert_iter.get_offset() <= self.start:
            self.delete_key_used = True
        else:
            self.delete_key_used = False
        if self.end - self.start > 1 or self.deleted_text in ("\r", "\n", " "):
            self.mergeable = False
        else:
            self.mergeable = True

def add_syntax_path(path_or_list):
    """ This function adds one (string) or many (list of strings) paths to the
        global search-paths for syntax-files. """
    global SYNTAX_PATH
    # handle list of strings
    if isinstance(path_or_list, (list, tuple)):
        for i in range(len(path_or_list)):
            SYNTAX_PATH.insert(0, path_or_list[-i])
    # handle single string
    elif isinstance(path_or_list, basestring):
        SYNTAX_PATH.insert(0, path_or_list)
    # handle attr-error
    else:
        raise TypeError, "Argument must be path-string or list of strings"

class Pattern:
    """ More or less internal used class representing a pattern. You may use
        this class to "hard-code" your syntax-definition. """

    def __init__(self, regexp, style="DEFAULT", group=0, flags=""):
        """ The constructor takes at least on argument: the regular-expression.

            The optional kwarg style defines the style applied to the string
            matched by the regexp.

            The kwarg group may be used to define which group of the regular
            expression will be used for highlighting (Note: This means that only
            the selected group will be highlighted but the complete pattern must
            match!)

            The optional kwarg flags specifies flags for the regular expression.
            Look at the Python lib-ref for a list of flags and there meaning."""
        # assemble re-flag
        flags += "ML"; flag   = 0

        _log_debug("init rule %s -> %s (%s)"%(regexp, style, flags))

        for char in flags:
            if char == 'M': flag |= re.M
            if char == 'L': flag |= re.L
            if char == 'S': flag |= re.S
            if char == 'I': flag |= re.I
            if char == 'U': flag |= re.U
            if char == 'X': flag |= re.X

        # compile re
        try: self._regexp = re.compile(regexp, flag)
        except re.error, e:
            raise Exception("Invalid regexp \"%s\": %s"%(regexp,str(e)))

        self._group  = group
        self.tag_name = style

    def __call__(self, txt, start, end):
        m = self._regexp.search(txt)
        if not m: return None

        #Do not use textiter as it can be invalidate while threading
        mstart, mend = m.start(self._group), m.end(self._group)
        s = start.copy(); s.forward_chars(mstart)
        e = start.copy(); e.forward_chars(mend)

        return (s,e)

class KeywordList(Pattern):
    """ This class may be used for hard-code a syntax-definition. It specifies
        a pattern for a keyword-list. This simplifies the definition of
        keyword-lists. """

    def __init__(self, keywords, style="keyword", flags=""):
        """ The constructor takes at least on argument: A list of strings
            specifying the keywords to highlight.

            The optional kwarg style specifies the style used to highlight these
            keywords.

            The optional kwarg flags specifies the flags for the
            (internal generated) regular-expression. """
        regexp = "(?:\W|^)(%s)\W"%("|".join(keywords),)
        Pattern.__init__(self, regexp, style, group=1, flags=flags)

class String:
    """ This class may be used to hard-code a syntax-definition. It simplifies
        the definition of a "string". A "string" is something that consists of
        a start-pattern and an end-pattern. The end-pattern may be content of
        the string if it is escaped. """

    def __init__(self, starts, ends, escape=None, style="string"):
        """ The constructor needs at least two arguments: The start- and
            end-pattern.

            The optional kwarg escape specifies a escape-sequence escaping the
            end-pattern.

            The optional kwarg style specifies the style used to highlight the
            string. """
        try:
            self._starts  = re.compile(starts)
        except re.error, e:
            raise Exception("Invalid regexp \"%s\": %s"%(regexp,str(e)))

        if escape:
            end_exp = "[^%(esc)s](?:%(esc)s%(esc)s)*%(end)s"
            end_exp = end_exp%{'esc':escape*2,'end':ends}
        else:
            end_exp = ends

        try:
            self._ends    = re.compile(end_exp)
        except re.error, e:
            raise Exception("Invalid regexp \"%s\": %s"%(regexp,str(e)))

        self.tag_name = style

    def __call__(self, txt, start, end):
        start_match = self._starts.search(txt)
        if not start_match: return

        start_it = start.copy()
        start_it.forward_chars(start_match.start(0))
        end_it   = end.copy()

        end_match = self._ends.search(txt, start_match.end(0)-1)
        #print end_match
        if end_match:
            end_it.set_offset(start.get_offset()+end_match.end(0))
        return  start_it, end_it

class LanguageDefinition:
    """ This class is a container class for all rules (Pattern, KeywordList,
        ...) specifying the language. You have to used this class if you like
        to hard-code your syntax-definition. """

    def __init__(self, rules):
      """ The constructor takes only one argument: A list of rules (i.e
          Pattern, KeywordList and String). """
      self._grammar = rules
      self._styles = dict()
      self.cache = {}


    def __call__(self, buf, start, end=None):
      # if no end given -> end of buffer
      if not end: end = buf.get_end_iter()

      try:
        return self.cache[(buf, start, end)]
      except KeyError:
        mstart = mend = end
        mtag   = None
        txt = buf.get_slice(start, end)

        # search min match
        for rule in self._grammar:
            # search pattern
            m = rule(txt, start, end)
            if not m: continue

            #print m
            # prefer match with smallest start-iter
            if (m[0].compare(mstart)) < 0:
                mstart, mend = m
                mtag = rule.tag_name
                continue

            if m[0].compare(mstart)==0 and m[1].compare(mend)>0:
            #if ((m[0] - mstart)==0) and ((m[1] - mend)>0):
                mstart, mend = m
                mtag = rule.tag_name
                continue

        self.cache[(buf, start, end)] = (mstart, mend, mtag)
        return (mstart, mend, mtag)

    def get_styles(self):
        return self._styles

class SyntaxLoader(ContentHandler, LanguageDefinition):
    """ This class loads a syntax definition. There have to be a file
        named LANGUAGENAME.xml in one of the directories specified in the
        global path-list. You may add a directory using the add_syntax_path()
        function. """

    # some translation-tables for the style-defs:
    style_weight_table =    {'ultralight': pango.WEIGHT_ULTRALIGHT,
                             'light': pango.WEIGHT_LIGHT,
                             'normal': pango.WEIGHT_NORMAL,
                             'bold':   pango.WEIGHT_BOLD,
                             'ultrabold': pango.WEIGHT_ULTRABOLD,
                             'heavy': pango.WEIGHT_HEAVY}
    style_variant_table =   {'normal': pango.VARIANT_NORMAL,
                             'smallcaps': pango.VARIANT_SMALL_CAPS}
    style_underline_table = {'none': pango.UNDERLINE_NONE,
                             'single': pango.UNDERLINE_SINGLE,
                             'double': pango.UNDERLINE_DOUBLE}
    style_style_table =     {'normal': pango.STYLE_NORMAL,
                             'oblique': pango.STYLE_OBLIQUE,
                             'italic': pango.STYLE_ITALIC}
    style_scale_table =     {
                            'xx_small': pango.SCALE_XX_SMALL,
                            'x_small':  pango.SCALE_X_SMALL,
                            'small':  pango.SCALE_SMALL,
                            'medium':  pango.SCALE_MEDIUM,
                            'large':  pango.SCALE_LARGE,
                            'x_large':  pango.SCALE_X_LARGE,
                            'xx_large': pango.SCALE_XX_LARGE,
                                                        }

    def __init__(self, lang_name):
        """ The constructor takes only one argument: the language name.
            The constructor tries to load the syntax-definition from a
            syntax-file in one directory of the global path-list.

            An instance of this class IS a LanguageDefinition. You can pass it
            to the constructor of the CodeBuffer class. """
        LanguageDefinition.__init__(self, [])
        ContentHandler.__init__(self)

        # search for syntax-files:
        fname = None
        for syntax_dir in SYNTAX_PATH:
            fname = os.path.join(syntax_dir, "%s.xml"%lang_name)
            if os.path.isfile(fname): break

        _log_debug("Loading syntaxfile %s"%fname)

        if not os.path.isfile(fname):
            raise Exception("No snytax-file for %s found!"%lang_name)

        xml.sax.parse(fname, self)

    # Dispatch start/end - document/element and chars
    def startDocument(self):
        self.__stack   = []

    def endDocument(self):
        del self.__stack

    def startElement(self, name, attr):
        self.__stack.append( (name, attr) )
        if hasattr(self, "start_%s"%name):
            handler = getattr(self, "start_%s"%name)
            handler(attr)

    def endElement(self, name):
        if hasattr(self, "end_%s"%name):
            handler = getattr(self, "end_%s"%name)
            handler()
        del self.__stack[-1]

    def characters(self, txt):
        if not self.__stack: return
        name, attr = self.__stack[-1]

        if hasattr(self, "chars_%s"%name):
            handler = getattr(self, "chars_%s"%name)
            handler(txt)

    # Handle regexp-patterns
    def start_pattern(self, attr):
        self.__pattern = ""
        self.__group   = 0
        self.__flags   = ''
        self.__style   = attr['style']
        if 'group' in attr.keys(): self.__group = int(attr['group'])
        if 'flags' in attr.keys(): self.__flags = attr['flags']

    def end_pattern(self):
        rule = Pattern(self.__pattern, self.__style, self.__group, self.__flags)
        self._grammar.append(rule)
        del self.__pattern
        del self.__group
        del self.__flags
        del self.__style

    def chars_pattern(self, txt):
        self.__pattern += unescape(txt)

    # handle keyword-lists
    def start_keywordlist(self, attr):
        self.__style = "keyword"
        self.__flags = ""
        if 'style' in attr.keys():
            self.__style = attr['style']
        if 'flags' in attr.keys():
            self.__flags = attr['flags']
        self.__keywords = []

    def end_keywordlist(self):
        kwlist = KeywordList(self.__keywords, self.__style, self.__flags)
        self._grammar.append(kwlist)
        del self.__keywords
        del self.__style
        del self.__flags

    def start_keyword(self, attr):
        self.__keywords.append("")

    def end_keyword(self):
        if not self.__keywords[-1]:
            del self.__keywords[-1]

    def chars_keyword(self, txt):
        parent,pattr = self.__stack[-2]
        if not parent == "keywordlist": return
        self.__keywords[-1] += unescape(txt)

    #handle String-definitions
    def start_string(self, attr):
        self.__style = "string"
        self.__escape = None
        if 'escape' in attr.keys():
            self.__escape = attr['escape']
        if 'style' in attr.keys():
            self.__style = attr['style']
        self.__start_pattern = ""
        self.__end_pattern = ""

    def end_string(self):
        strdef = String(self.__start_pattern, self.__end_pattern,
                        self.__escape, self.__style)
        self._grammar.append(strdef)
        del self.__style
        del self.__escape
        del self.__start_pattern
        del self.__end_pattern

    def chars_starts(self, txt):
        self.__start_pattern += unescape(txt)

    def chars_ends(self, txt):
        self.__end_pattern += unescape(txt)

    # handle style
    def start_style(self, attr):
        self.__style_props = dict()
        self.__style_name = attr['name']

    def end_style(self):
        self._styles[self.__style_name] = self.__style_props
        del self.__style_props
        del self.__style_name

    def start_property(self, attr):
        self.__style_prop_name = attr['name']

    def chars_property(self, value):
        value.strip()

        # convert value
        if self.__style_prop_name in ['font','foreground','background',]:
            pass

        elif self.__style_prop_name == 'variant':
            if not value in self.style_variant_table.keys():
                Exception("Unknown style-variant: %s"%value)
            value = self.style_variant_table[value]

        elif self.__style_prop_name == 'underline':
            if not value in self.style_underline_table.keys():
                Exception("Unknown underline-style: %s"%value)
            value = self.style_underline_table[value]

        elif self.__style_prop_name == 'scale':
            if not value in self.style_scale_table.keys():
                Exception("Unknown scale-style: %s"%value)
            value = self.style_scale_table[value]

        elif self.__style_prop_name == 'weight':
            if not value in self.style_weight_table.keys():
                Exception("Unknown style-weight: %s"%value)
            value = self.style_weight_table[value]

        elif self.__style_prop_name == 'style':
            if not value in self.style_style_table[value]:
                Exception("Unknwon text-style: %s"%value)
            value = self.style_style_table[value]

        else:
            raise Exception("Unknown style-property %s"%self.__style_prop_name)

        # store value
        self.__style_props[self.__style_prop_name] = value

class CodeBuffer(gtk.TextBuffer):
    """ This class extends the gtk.TextBuffer to support syntax-highlighting.
        You can use this class like a normal TextBuffer. """

    # some translation-tables for the style-defs:
    style_weight_table =    {'ultralight': pango.WEIGHT_ULTRALIGHT,
                             'light': pango.WEIGHT_LIGHT,
                             'normal': pango.WEIGHT_NORMAL,
                             'bold':   pango.WEIGHT_BOLD,
                             'ultrabold': pango.WEIGHT_ULTRABOLD,
                             'heavy': pango.WEIGHT_HEAVY}
    style_variant_table =   {'normal': pango.VARIANT_NORMAL,
                             'smallcaps': pango.VARIANT_SMALL_CAPS}
    style_underline_table = {'none': pango.UNDERLINE_NONE,
                             'single': pango.UNDERLINE_SINGLE,
                             'double': pango.UNDERLINE_DOUBLE}
    style_style_table =     {'normal': pango.STYLE_NORMAL,
                             'oblique': pango.STYLE_OBLIQUE,
                             'italic': pango.STYLE_ITALIC}
    style_scale_table =     {
                            'xx_small': pango.SCALE_XX_SMALL,
                            'x_small':  pango.SCALE_X_SMALL,
                            'small':  pango.SCALE_SMALL,
                            'medium':  pango.SCALE_MEDIUM,
                            'large':  pango.SCALE_LARGE,
                            'x_large':  pango.SCALE_X_LARGE,
                            'xx_large': pango.SCALE_XX_LARGE,
                                                                                                                }
    def style_convert(self,style):
      """ Covert text value to pango style
         (required as theme is stocked as full text xml)"""
      for style_tag, style_value in style.iteritems():
        # convert value
#        print style_tag,style_value,type(style_value)
        if style_tag in ['font','foreground','background',]:
            continue
        elif type(style_value) not in (str,unicode):
            continue
        elif style_tag == 'variant':
            if not style_value in self.style_variant_table.keys():
                Exception("Unknown style-variant: %s"%style_value)
            style[style_tag] = self.style_variant_table[style_value]

        elif style_tag == 'underline':
            if not style_value in self.style_underline_table.keys():
                Exception("Unknown underline-style: %s"%style_value)
            style[style_tag] = self.style_underline_table[style_value]

        elif style_tag == 'scale':
            if not style_value in self.style_scale_table.keys():
                Exception("Unknown scale-style: %s"%style_value)
            style[style_tag] = self.style_scale_table[style_value]

        elif style_tag == 'weight':
            if not style_value in self.style_weight_table.keys():
                Exception("Unknown style-weight: %s"%style_value)
            style[style_tag] = self.style_weight_table[style_value]

        elif style_tag == 'style':
            if not style_value in self.style_style_table[style_value]:
                Exception("Unknwon text-style: %s"%style_value)
            style[style_tag] = self.style_style_table[style_value]

        else:
            raise Exception("Unknown style-property %s"%style_tag)
      return style

    def __init__(self, table=None, lang=None, styles={}):
        """ The constructor takes 3 optional arguments.

            table specifies a tag-table associated with the TextBuffer-instance.
            This argument will be passed directly to the constructor of the
            TextBuffer-class.

            lang specifies the language-definition. You have to load one using
            the SyntaxLoader-class or you may hard-code your syntax-definition
            using the LanguageDefinition-class.

            styles is a dictionary used to extend or overwrite the default styles
            provided by this module (DEFAULT_STYLE) and any language specific
            styles defined by the LanguageDefinition. """
        gtk.TextBuffer.__init__(self, table)

        self.defer = _DeferClass()

        #undo stack init
        self.undo_stack = []
        self.redo_stack = []
        self.not_undoable_action = False
        self.undo_in_progress = False

        # default styles
        self.styles = DEFAULT_STYLES

        # update styles with lang-spec:
        if lang:
            self.styles.update(lang.get_styles())
        # update styles with user-defined
        self.styles.update(styles)

        # create tags
        for name, props in self.styles.items():
            style = dict(self.styles['DEFAULT'])    # take default
            style.update(props)                     # and update with props

            # convert value
            style = self.style_convert(style)
            # create tag
#            print 'create tag : ',name,' style : ',style
            self.create_tag(name, **style)

        # store lang-definition
        self._lang_def = lang

        self.connect("insert-text",self._on_insert_text_undo)
        self.connect_after("insert-text", self._on_insert_text_hilight)
        self.connect("delete-range",self._on_delete_range_undo)
        self.connect_after("delete-range", self._on_delete_range_hilight)
#        self.connect('apply-tag', self._on_apply_tag)
#        self._apply_tags = False

    @property
    def can_undo(self):
        return bool(self.undo_stack)

    @property
    def can_redo(self):
        return bool(self.redo_stack)

    def _remove_tag_idle(self,widget,start,end):
        self.defer(self._remove_tag,widget,start,end)

    def _remove_tag(self,widget,start,end):
        self.remove_all_tags(self.get_iter_at_offset(start),self.get_iter_at_offset(end))

    def _on_apply_tag(self, buf, tag, start, end):
        if not self._apply_tags:
            self.emit_stop_by_name('apply-tag')
            return True

    def _insert_space(self,buf,line,nb):
      it = buf.get_iter_at_line_index(line,0)
      buf.insert(it,nb*' ')

    def _on_insert_text_hilight(self, buf, it, text, length):
        #auto indent
        if text=='\n':
          line=it.get_line()
          itc = buf.get_iter_at_line_index(line-1,0)
          if itc.get_char()==' ':
            if (itc.forward_find_char(lambda x, d: x not in ' \n')):
              nb_space = itc.get_line_offset()
              self.defer(self._insert_space,buf,line,nb_space)

        # if no syntax defined -> nop
        if not self._lang_def:
            _log_debug('language not defined')
            return False

        it = it.copy()
        it.backward_chars(length)
        if not it.begins_tag():
            it.backward_to_tag_toggle(None)
#            _log_debug("Not tag-start -> moved iter to %i (%s)"%(it.get_offset(), it.get_char()))

        if it.begins_tag(self.get_tag_table().lookup("DEFAULT")):
            it.backward_to_tag_toggle(None)
#            _log_debug("Iter at DEFAULT-start -> moved to %i (%s)"%(it.get_offset(), it.get_char()))

#        hildon.hildon_gtk_window_set_progress_indicator(self.get_parent().get_parent().get_parent(), True)
        self.defer(self.update_syntax_idle,it.get_offset())

    def _on_insert_text_undo(self, buf, it, text, length):
        #undo managing
        def can_be_merged(prev, cur):
            """see if we can merge multiple inserts here

            will try to merge words or whitespace
            can't merge if prev and cur are not mergeable in the first place
            can't merge when user set the input bar somewhere else
            can't merge across word boundaries"""
            WHITESPACE = (' ', '\t')
            if not cur.mergeable or not prev.mergeable:
                return False
            elif cur.offset != (prev.offset + prev.length):
                return False
            elif cur.text in WHITESPACE and not prev.text in WHITESPACE:
                return False
            elif prev.text in WHITESPACE and not cur.text in WHITESPACE:
                return False
            return True

        if not self.undo_in_progress:
            self.redo_stack = []
        if self.not_undoable_action:
            return
        undo_action = UndoableInsert(it, text, length)
        try:
            prev_insert = self.undo_stack.pop()
        except IndexError:
            self.undo_stack.append(undo_action)
            return
        if not isinstance(prev_insert, UndoableInsert):
            self.undo_stack.append(prev_insert)
            self.undo_stack.append(undo_action)
            return
        if can_be_merged(prev_insert, undo_action):
            prev_insert.length += undo_action.length
            prev_insert.text += undo_action.text
            self.undo_stack.append(prev_insert)
        else:
            self.undo_stack.append(prev_insert)
            self.undo_stack.append(undo_action)

    def _on_insert_text(self, buf, it, text, length):
        self._on_insert_text_hilight(buf, it, text, length)
        return self._on_insert_text_undo(buf, it, text, length)

    def _on_delete_range_hilight(self, buf, start, end):
        # if no syntax defined -> nop
        if not self._lang_def: return False

        start = start.copy()
        if not start.begins_tag():
            start.backward_to_tag_toggle(None)

        self.defer(self.update_syntax_idle,start.get_offset())

    def _on_delete_range_undo(self, text_buffer, start_iter, end_iter):
        def can_be_merged(prev, cur):
            """see if we can merge multiple deletions here

            will try to merge words or whitespace
            can't merge if delete and backspace key were both used
            can't merge across word boundaries"""

            WHITESPACE = (' ', '\t')
            if prev.delete_key_used != cur.delete_key_used:
                return False
            if prev.start != cur.start and prev.start != cur.end:
                return False
            if cur.deleted_text not in WHITESPACE and \
               prev.deleted_text in WHITESPACE:
                return False
            elif cur.deleted_text in WHITESPACE and \
               prev.deleted_text not in WHITESPACE:
                return False
            return True

        if not self.undo_in_progress:
            self.redo_stack = []
        if self.not_undoable_action:
            return
        undo_action = UndoableDelete(text_buffer, start_iter, end_iter)
        try:
            prev_delete = self.undo_stack.pop()
        except IndexError:
            self.undo_stack.append(undo_action)
            return
        if not isinstance(prev_delete, UndoableDelete):
            self.undo_stack.append(prev_delete)
            self.undo_stack.append(undo_action)
            return
        if can_be_merged(prev_delete, undo_action):
            if prev_delete.start == undo_action.start: # delete key used
                prev_delete.deleted_text += undo_action.deleted_text
                prev_delete.end += (undo_action.end - undo_action.start)
            else: # Backspace used
                prev_delete.deleted_text = "%s%s" % (undo_action.deleted_text,
                                                     prev_delete.deleted_text)
                prev_delete.start = undo_action.start
            self.undo_stack.append(prev_delete)
        else:
            self.undo_stack.append(prev_delete)
            self.undo_stack.append(undo_action)

    def _on_delete_range(self, buf, start, end):
        self._on_delete_range_undo(buf, start, end)
        self._on_delete_range_hilight(buf, start, end)

    def begin_not_undoable_action(self):
        """don't record the next actions

        toggles self.not_undoable_action"""
        self.not_undoable_action = True

    def end_not_undoable_action(self):
        """record next actions

        toggles self.not_undoable_action"""
        self.not_undoable_action = False

    def undo(self):
        """undo inserts or deletions

        undone actions are being moved to redo stack"""
        if not self.undo_stack:
            return
        self.begin_not_undoable_action()
        self.undo_in_progress = True
        undo_action = self.undo_stack.pop()
        self.redo_stack.append(undo_action)
        if isinstance(undo_action, UndoableInsert):
            _log_debug('doing delete')
            start = self.get_iter_at_offset(undo_action.offset)
            stop = self.get_iter_at_offset(
                undo_action.offset + undo_action.length
            )
            self.delete(start, stop)
            self.place_cursor(start)
        else:
            start = self.get_iter_at_offset(undo_action.start)
            stop = self.get_iter_at_offset(undo_action.end)
            self.insert(start, undo_action.deleted_text)
            if undo_action.delete_key_used:
                self.place_cursor(start)
            else:
                self.place_cursor(stop)
        self.end_not_undoable_action()
        self.undo_in_progress = False

    def redo(self):
        """redo inserts or deletions

        redone actions are moved to undo stack"""
        if not self.redo_stack:
            return
        self.begin_not_undoable_action()
        self.undo_in_progress = True
        redo_action = self.redo_stack.pop()
        self.undo_stack.append(redo_action)
        if isinstance(redo_action, UndoableInsert):
            start = self.get_iter_at_offset(redo_action.offset)
            self.insert(start, redo_action.text)
            new_cursor_pos = self.get_iter_at_offset(
                redo_action.offset + redo_action.length
            )
            self.place_cursor(new_cursor_pos)
        else:
            start = self.get_iter_at_offset(redo_action.start)
            stop = self.get_iter_at_offset(redo_action.end)
            self.delete(start, stop)
            self.place_cursor(start)
        self.end_not_undoable_action()
        self.undo_in_progress = False

    def completed(self,widget):
        self.apply_tag = False

    def apply_tag_by_name_offset_idle(self,widget,tagname,s,e):
        self.defer(self.apply_tag_by_name_offset,widget,tagname,s,e)

    def apply_tag_by_name_offset(self,widget,tagname,s,e):
        #print tagname,s,e
        self.apply_tag_by_name(tagname,self.get_iter_at_offset(s),self.get_iter_at_offset(e))

    def update_syntax_idle(self,ostart,oend=None):
        #no language defined
        if not self._lang_def:
            return
#        hildon.hildon_gtk_window_set_progress_indicator(self.get_parent().get_parent().get_parent(), True)
        # if not end defined
        if not oend:
            end = self.get_end_iter()
        else:
            end = self.get_iter_at_offset(oend)
        start = self.get_iter_at_offset(ostart)

        # search first rule matching txt[start..end]
        mstart, mend, tagname = self._lang_def(self, start, end)

        # optimisation: if mstart-mend is allready tagged with tagname
        #   -> finished
        if tagname:     #if something found
            tag = self.get_tag_table().lookup(tagname)
            if mstart.begins_tag(tag) and mend.ends_tag(tag) and not mstart.equal(start):
#                self._apply_tags = True
                self.remove_all_tags(start,mstart)
                self.apply_tag_by_name("DEFAULT", start, mstart)
#                self._apply_tags = False
                return

        # remove all tags from start..mend (mend == buffer-end if no match)
#        self._apply_tags=True
        self.remove_all_tags(start, mend)
        # make start..mstart = DEFAUL (mstart == buffer-end if no match)
        if not start.equal(mstart):
            #_log_debug("Apply DEFAULT")
            self.apply_tag_by_name("DEFAULT", start, mstart)
#        self._apply_tags=False
        # nothing found -> finished
        if not tagname:
           finished = True
           return

        # apply tag
        #_log_debug("Apply %s"%tagname)
#        self._apply_tags=True
        self.apply_tag_by_name(tagname, mstart, mend)
#        self._apply_tags=False
        start = mend

        if start == end:
            finished = True
#            hildon.hildon_gtk_window_set_progress_indicator(self.get_parent().get_parent().get_parent(), False)
            return

        self.defer(self.update_syntax_idle,start.get_offset(),end.get_offset())

    def reset_language(self, lang_def):
        """ Reset the currently used language-definition. """
        # remove all tags from complete text
        start = self.get_start_iter()
        self.remove_all_tags(start, self.get_end_iter())
        # store lexer
        self._lang_def = lang_def
        # update styles from lang_def:
        if self._lang_def:
            self.update_styles(self._lang_def.get_styles())
        # and ...
#        self._apply_tags = True
        self.defer(self.update_syntax_idle,start.get_offset)
#        self._apply_tags = False

    def update_styles(self, styles):
        """ Update styles. This method may be used to reset any styles at
            runtime. """
        self.styles.update(styles)
#        print styles

        table = self.get_tag_table()
        for name, props in styles.items():
            style = self.styles['DEFAULT'].copy()
            style.update(props)
            #convert text value
            style = self.style_convert(style)

            # if tagname is unknown:
            if not table.lookup(name):
                #_log_debug("Create tag: %s (%s)"%(name, style))
                self.create_tag(name, **style)
            else: # update tag
                tag = table.lookup(name)
                #_log_debug("Update tag %s with (%s)"%(name, style))
#                print ("Update tag %s with (%s)"%(name, style))
                map(lambda i: tag.set_property(i[0],i[1]), style.items())
