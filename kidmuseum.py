from datetime import datetime
import time
import urllib2
import json


class TxtConversation(object):
    """
    A TxtConversation manages a text conversation between a person txting you and your program.
    It comes with a bunch of useful functions that help you communicate with the person texting
    your program, in particular sending messages and getting information from the user of your
    program.

    It also handles registering your program with the Texting Playground.  This allows people to
    pick your program to chat with by sending a txt with the name of your program.

    Here's a simple example of what a program could look like:

        from kidmuseum import TxtConversation

        conversation = TxtConversation("I <3 compliments")
        converstaion.send_message("Hi! You love compliments?  Well I got tons of 'em!")

        name = converstaion.get_string("First, what's your name?")

        conversation.send_message("Hey, " + name + " is an awesome name!")
        conversation.send_message("I bet you're super smart too.")
        conversation.send_message("To be honest, you're the coolest person I've talked today BY FAR :D")
        converstaion.send_message("Gotta go, ttyl!")

    Now, let's pretend the phone number for the SMS Playground was 240-555-0033.  Here's what the
    conversation would look like If someone texted I <3 compliments to that number.

        Person:   I <3 compliments
        Program:  Hi! You love compliments?  Well I got tons of 'em!
        Program:  First, what's your name?
        Person:   Sarah
        Program:  Hey, Sarah is an awesome name!
        Program:  I bet you're super smart too.
        Program:  To be honest, you're the coolest person I've talked today BY FAR :D
        Program:  Gotta go, ttyl!
    """

    def __init__(self, keyword):
        """
        This is the code that get's called when you create the conversation.  In the example above,
        the code would be: TxtConversation("I <3 compliments").

        :param keyword: What someone would text to start this conversation?
        """
        self.conversation_code = start_a_conversation(keyword)

    def send_message(self, message):
        send_message(self.conversation_code, message)

    def send_picture(self, picture_url, message=""):
        send_message(self.conversation_code, message, picture_url)

    def get_string(self, prompt_message):
        self.send_message(prompt_message)
        return get_response_message(self.conversation_code, "string")

    def get_integer(self, prompt_message):
        self.send_message(prompt_message)
        return get_response_message(self.conversation_code, "int")

    def get_floating_point(self, prompt_message):
        self.send_message(prompt_message)
        return get_response_message(self.conversation_code, "float")

    def get_picture(self, prompt_message):
        self.send_message(prompt_message)
        return get_response_message(self.conversation_code, "picture")

# ----------------------------------------------------------------------------
# Functions for communicating with the Texting Playground server
# ----------------------------------------------------------------------------

start_conversation_url = "http://localhost:5000/conversation/start"
send_message_url = "http://localhost:5000/conversation/{}/message/send"
get_response_message_url = "http://localhost:5000/conversation/{}/message/response/{}"


def start_a_conversation(keyword):
    timeout_seconds = 120
    start_time = datetime.utcnow()

    while (True):
        # Ask the server to start a conversation with someone
        # who texts the keyword to the Texting Playground's phone number
        request = urllib2.Request(start_conversation_url, json.dumps({
            'keyword': keyword,
            'messages_must_be_older_than': str(start_time),
        }), {'Content-Type': 'application/json'})
        response_data = json.loads(urllib2.urlopen(request).read())

        # If nobody has texted our keyword to the Texting Playgroud yet,
        # wait a bit and check again.  If it's been a really long time,
        # stop waiting and stop the program.
        if 'wait_for_seconds' in response_data:
            time.sleep(response_data['wait_for_seconds'])
            if (datetime.utcnow() - start_time).seconds >= timeout_seconds:
                raise Exception("Too much time passed while waiting for text with {}.".format(keyword))
            continue

        # return the special conversation code used to communicated with
        # the user who started the conversation
        return response_data['conversation_code']


def send_message(conversation_code, message, picture_url=None):
    # Tell the server to send a text message to the user in the conversation
    request = urllib2.Request(send_message_url.format(conversation_code), json.dumps({
        'message': message,
        'picture_url': picture_url,
    }), {'Content-Type': 'application/json'})
    response = urllib2.urlopen(request)

    # If the server told us something was wrong with our request,
    # stop the program
    if response.getcode() != 200:
        raise Exception("Failed to send message: {}".format(response.read()))


def get_response_message(conversation_code, response_type):
    timeout_seconds = 120
    start_time = datetime.utcnow()

    while (True):
        # Ask the server for the message the user sent to respond
        # to our last message sent to them
        url = get_response_message_url.format(conversation_code, response_type)
        request = urllib2.Request(url, json.dumps({
            'messages_must_be_older_than': str(start_time),
        }), {'Content-Type': 'application/json'})
        response_data = json.loads(urllib2.urlopen(request).read())

        # If the user hasn't responded yet, wait a bit and check again.
        # If it's been a really long time, stop waiting and stop the program.
        if 'wait_for_seconds' in response_data:
            time.sleep(response_data['wait_for_seconds'])
            if (datetime.utcnow() - start_time).seconds >= timeout_seconds:
                raise Exception("Too much time passed while waiting for a response")
            continue

        # return the special conversation code used to communicated with
        # the user who started the conversation
        if response_type == "picture":
            return response_data['url']
        else:
            return response_data['message']
