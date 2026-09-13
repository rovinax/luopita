import enum
class MassageType(enum.Enum):
    Notion = 1
    Picture = 2
    File = 3
    Video = 4
    Text = 5

class MessageTag(enum.Enum):
    Text = '[text]'
    Command = '[command]'
    TextOver = '[text^over]'
