# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from collections import defaultdict
from difflib import unified_diff
from os import remove
from os.path import dirname, join
from pipes import quote
from tempfile import mkstemp

from blockwart.exceptions import BundleError
from blockwart.items import Item, ItemStatus
from blockwart.items.directories import validator_mode
from blockwart.utils import cached_property, LOG, sha1
from blockwart.utils.remote import PathInfo
from blockwart.utils.text import mark_for_translation as _
from blockwart.utils.text import bold, green, red


def content_processor_mako(item):
    from mako.lookup import TemplateLookup
    lookup = TemplateLookup(
        directories=[item.item_dir],
        input_encoding='utf-8',
        output_encoding='utf-8',
    )
    template = lookup.get_template(item.attributes['source'])
    return template.render(item=item, bundle=item.bundle, node=item.node,
                           repo=item.node.repo)

CONTENT_PROCESSORS = {
    'binary': None,
    'mako': content_processor_mako,
}


def diff(content_old, content_new, filename):
    output = ""
    for line in unified_diff(
        content_old.splitlines(True),
        content_new.splitlines(True),
        fromfile=filename,
        tofile=_("<blockwart content>"),
    ):
        try:
            line = line.decode('UTF-8')
        except UnicodeDecodeError:
            line = line[0] + _("<line not encoded in UTF-8>")
        line = line.rstrip("\n")
        if line.startswith("+"):
            line = green(line)
        elif line.startswith("-"):
            line = red(line)
        output += line + "\n"
    return output


def get_remote_file_contents(node, path):
    """
    Returns the contents of the given path as a string.
    """
    handle, tmp_file = mkstemp()
    node.download(path, tmp_file)
    with open(tmp_file) as f:
        content = f.read()
    remove(tmp_file)
    return content


def hash_local_file(path):
    """
    Retuns the sha1 hash of a file on the local machine.
    """
    with open(path, 'rb') as f:
        sha1_hash = sha1(f.read())
    return sha1_hash


def validator_content_type(item_id, value):
    if value not in CONTENT_PROCESSORS:
        raise BundleError(
            _("invalid content_type for {}: '{}'").format(item_id, value)
        )


ATTRIBUTE_VALIDATORS = defaultdict(lambda: lambda id, value: None)
ATTRIBUTE_VALIDATORS.update({
    'content_type': validator_content_type,
    'mode': validator_mode,
})


class File(Item):
    """
    A file.
    """
    BUNDLE_ATTRIBUTE_NAME = "files"
    DEPENDS_STATIC = ["directory:"]
    ITEM_ATTRIBUTES = {
        'content': None,
        'content_type': "mako",
        'group': "root",
        'mode': "0664",
        'owner': "root",
        'source': None,
    }
    ITEM_TYPE_NAME = "file"

    def __repr__(self):
        return "<File path:{} owner:{} group:{} mode:{} content_hash:{}>".format(
            quote(self.name),
            self.attributes['owner'],
            self.attributes['group'],
            self.attributes['mode'],
            self.content_hash,
        )

    @cached_property
    def content(self):
        return CONTENT_PROCESSORS[self.attributes['content_type']](self)

    @cached_property
    def content_hash(self):
        if self.attributes['content_type'] == 'binary':
            return hash_local_file(self.template)
        else:
            return sha1(self.content)

    @cached_property
    def template(self):
        return join(self.item_dir, self.attributes['source'])

    def ask(self, status):
        if 'type' in status.info['needs_fixing']:
            if not status.info['path_info'].exists:
                return _("Doesn't exist.")
            else:
                return "{} {} → {}\n".format(
                    bold(_("type")),
                    status.info['path_info'].desc,
                    _("file"),
                )

        question = ""

        if 'content' in status.info['needs_fixing']:
            question += bold(_("content "))
            if status.info['path_info'].is_text_file and \
                    not self.attributes['content_type'] == 'binary':
                content_is = get_remote_file_contents(self.node, self.name)
                content_should = self.content
                question += "\n" + diff(content_is, content_should, self.name) + "\n"
            else:
                question += "'{}' → {}".format(
                    status.info['path_info'].desc,
                    _("<blockwart content>"),
                )

        if 'mode' in status.info['needs_fixing']:
            question += "{} {} → {}\n".format(
                bold(_("mode")),
                status.info['path_info'].mode,
                self.attributes['mode'],
            )

        if 'owner' in status.info['needs_fixing']:
            question += "{} {} → {}\n".format(
                bold(_("owner")),
                status.info['path_info'].owner,
                self.attributes['owner'],
            )

        if 'group' in status.info['needs_fixing']:
            question += "{} {} → {}\n".format(
                bold(_("group")),
                status.info['path_info'].group,
                self.attributes['group'],
            )

        return question.rstrip("\n")

    def fix(self, status):
        for fix_type in ('type', 'content', 'mode', 'owner', 'group'):
            if fix_type in status.info['needs_fixing']:
                if fix_type == 'group' and \
                        'owner' in status.info['needs_fixing']:
                    # owner and group are fixed with a single chown
                    continue
                if fix_type in ('mode', 'owner', 'group') and \
                        'content' in status.info['needs_fixing']:
                    # fixing content implies settings mode and owner/group
                    continue
                LOG.info(_("{}:{}: fixing {}...").format(
                    self.node.name,
                    self.id,
                    fix_type,
                ))
                getattr(self, "_fix_" + fix_type)(status)

    def _fix_content(self, status):
        if self.attributes['content_type'] == 'binary':
            local_path = self.template
        else:
            handle, local_path = mkstemp()
            with open(local_path, 'w') as f:
                f.write(self.content)
        try:
            self.node.upload(local_path, self.name)
            self._fix_mode(status)
            self._fix_owner(status)
        finally:
            if self.attributes['content_type'] != 'binary':
                remove(local_path)

    def _fix_mode(self, status):
        self.node.run("chmod {} {}".format(
            self.attributes['mode'],
            quote(self.name),
        ))

    def _fix_owner(self, status):
        self.node.run("chown {}:{} {}".format(
            quote(self.attributes['owner']),
            quote(self.attributes['group']),
            quote(self.name),
        ))
    _fix_group = _fix_owner

    def _fix_type(self, status):
        self.node.run("rm -rf {}".format(quote(self.name)))
        self.node.run("mkdir -p {}".format(quote(dirname(self.name))))
        self._fix_content(status)

    def get_status(self):
        correct = True
        path_info = PathInfo(self.node, self.name)
        status_info = {'needs_fixing': [], 'path_info': path_info}

        if not path_info.is_file:
            status_info['needs_fixing'].append('type')
        else:
            if path_info.sha1 != self.content_hash:
                status_info['needs_fixing'].append('content')
            if path_info.mode != self.attributes['mode']:
                status_info['needs_fixing'].append('mode')
            if path_info.owner != self.attributes['owner']:
                status_info['needs_fixing'].append('owner')
            if path_info.group != self.attributes['group']:
                status_info['needs_fixing'].append('group')

        if status_info['needs_fixing']:
            correct = False
        return ItemStatus(correct=correct, info=status_info)

    def validate_attributes(self, attributes):
        for key, value in attributes.items():
            ATTRIBUTE_VALIDATORS[key](self.id, value)
