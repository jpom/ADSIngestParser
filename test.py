import sys
import json

#from adsingestp.parsers.ieee import IEEEParser
#from adsingestp.parsers.jats import JATSParser

#from adsingestp.parsers.springer import SpringerParser
#from adsingestp.parsers.bits import BitsParser

#from adsingestp.parsers.dubcore import DublinCoreParser
from adsingestp.parsers.xoai import XOAIParser

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <path-to-xml-file>")
    sys.exit(1)
mytest = sys.argv[1]

with open(mytest, "r") as fin:
    raw = fin.read()

#parser = IEEEParser()
#parser = JATSParser()

#parser = SpringerParser()
#parser = BitsParser()

#parser = DublinCoreParser()
parser = XOAIParser()

output = parser.parse(raw)
if output:
    print("%s" % json.dumps(output, indent=2, sort_keys=True))
    print("\n")
