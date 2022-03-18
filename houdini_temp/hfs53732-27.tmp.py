# current node
node = kwargs["node"]
# print(kwargs)
# current parameter
pt = kwargs["parmtuple"]

# its template
template = pt.parmTemplate()

# parameter's tags
tags = template.tags()
print(tags)
# invert collapsed tag
tags['editor'] = str(1-int(tags['editor']))

# invert icon
tags['script_action_icon'] = "KEYS_Up" if tags['script_action_icon'] == "KEYS_Down" else "KEYS_Down"

# set updated tags
template.setTags(tags)

# replace parameter template with updated tags
group = node.parmTemplateGroup()
group.replace(pt.name(), template)
node.setParmTemplateGroup(group)
