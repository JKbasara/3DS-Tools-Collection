#!/usr/bin/env python3

#script is modified from https://github.com/Relys/3DS_Multi_Decryptor/blob/master/to3DS/CDNto3DS/CDNto3DS.py
#requires PyCrypto to be installed ("python3 -m ensurepip" then "pip3 install PyCrypto")
#requires makerom (https://github.com/profi200/Project_CTR/releases)
#this is a Python 3 script

import os
import platform
import struct
import errno
import sys
import shlex
import urllib.request, urllib.error, urllib.parse
from subprocess import DEVNULL, STDOUT, call, check_call
from struct import unpack, pack
from subprocess import call
from binascii import hexlify, unhexlify
from hashlib import sha256
from Crypto.Cipher import AES

##########From http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python/377028#377028
def which(program):
    import os
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc: # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise

##########Based on https://stackoverflow.com/questions/5783517/downloading-progress-bar-urllib2-python
def chunk_report(bytes_so_far, chunk_size, total_size):
    percent = float(bytes_so_far) / total_size
    percent = round(percent*100, 2)
    sys.stdout.write('\rDownloaded and decrypted %d of %d bytes (%0.2f%%)' % (bytes_so_far, total_size, percent))
    sys.stdout.flush()

    if bytes_so_far >= total_size:
        print('\n')

# download in 0x200000 byte chunks, decrypt the chunk with IVs described below, then write the decrypted chunk to disk (half the file size of decrypting separately!)
def chunk_read(response, outfname, intitlekey, first_iv, chunk_size=0x200000, report_hook=None):
    fh = open(outfname,'wb')
    total_size = int(response.getheader('Content-Length'))
    total_size = int(total_size)
    bytes_so_far = 0
    data = []
    first_chunk_read = 0

    while 1:
        if report_hook:
            report_hook(bytes_so_far, chunk_size, total_size)

        chunk = response.read(chunk_size)
        bytes_so_far += len(chunk)

        if not chunk:
             break

        # IV of first chunk should be the Content ID + 28 0s like with the entire file, but each subsequent chunk should be the last 16 bytes of the previous still ciphered chunk
        if first_chunk_read == 0:
            decryptor = AES.new(intitlekey, AES.MODE_CBC, unhexlify(first_iv))
            first_chunk_read = 1
        else:
            decryptor = AES.new(intitlekey, AES.MODE_CBC, prev_chunk[(0x200000 - 16):0x200000])

        dec_chunk = decryptor.decrypt(chunk)
        prev_chunk = chunk

        fh.write(dec_chunk)

    fh.close()

def SystemUsage():
    print('Usage: python3 PlaiCDN.py <TitleID TitleKey [-redown -redec -no3ds -nocia] or [-check]> or [-deckey] or [-checkbin]')
    print('-deckey   : print keys from decTitleKeys.bin')
    print('-check    : checks if title id matches key')
    print('-checkbin : checks titlekeys from decTitleKeys.bin')
    print('-redown   : redownload content')
    print('-redec    : re-attempt content decryption')
    print('-no3ds    : don\'t build 3DS file')
    print('-nocia    : don\'t build CIA file')
    raise SystemExit(0)

#from https://github.com/Relys/3DS_Multi_Decryptor/blob/master/ticket-titlekey_stuff/printKeys.py
for i in range(len(sys.argv)):
    if sys.argv[i] == '-deckey':
        with open('decTitleKeys.bin', 'rb') as fh:
            nEntries = os.fstat(fh.fileno()).st_size / 32
            fh.seek(16, os.SEEK_SET)
            for i in range(int(nEntries)):
                fh.seek(8, os.SEEK_CUR)
                titleId = fh.read(8)
                decryptedTitleKey = fh.read(16)
                print('%s: %s' % ((hexlify(titleId)).decode(), (hexlify(decryptedTitleKey)).decode()))
        raise SystemExit(0)

for i in range(len(sys.argv)):
    if sys.argv[i] == '-checkbin':
        with open('decTitleKeys.bin', 'rb') as fh:
            nEntries = os.fstat(fh.fileno()).st_size / 32
            fh.seek(16, os.SEEK_SET)
            print('This option checks 00040000 (game) and 0004008c (dlc) titles only\n')
            for i in range(int(nEntries)):
                fh.seek(8, os.SEEK_CUR)
                titleId = fh.read(8)
                decryptedTitleKey = fh.read(16)

                baseurl = 'http://nus.cdn.c.shop.nintendowifi.net/ccs/download/' + (hexlify(titleId)).decode()
                # if it's not a game or dlc, skip to the next title
                if (hexlify(titleId)).decode()[:8] != '00040000' and (hexlify(titleId)).decode()[:8] != '0004008c':
                    continue
                else:
                    try:
                        tmd = urllib.request.urlopen(baseurl + '/tmd')
                    except urllib.error.URLError as e:
                        continue

                    tmd = tmd.read()
                    contentCount = unpack('>H', tmd[0x206:0x208])[0]
                    for i in range(contentCount):
                        cOffs = 0xB04+(0x30*i)
                        cID = format(unpack('>I', tmd[cOffs:cOffs+4])[0], '08x')
                        # use range requests to download bytes 0 through 271, needed 272 instead of 260 because AES-128-CBC encrypts in chunks of 128 bits
                        try:
                            checkReq = urllib.request.Request('%s/%s'%(baseurl, cID))
                            checkReq.headers['Range'] = 'bytes=%s-%s' % (0, 271)
                            checkTemp = urllib.request.urlopen(checkReq)
                        except urllib.error.URLError as e:
                            continue

                    # set IV to offset 0xf0 length 0x10 of ciphertext; thanks to yellows8 for the offset
                    checkTempPerm = checkTemp.read()
                    checkIv = checkTempPerm[0xf0:0x100]
                    decryptor = AES.new(decryptedTitleKey, AES.MODE_CBC, checkIv)

                    # check for magic ('NCCH') at offset 0x100 length 0x104 of the decrypted content
                    checkTempOut = decryptor.decrypt(checkTempPerm)[0x100:0x104]
                    if 'NCCH' in checkTempOut.decode('UTF-8', 'ignore'):
                        print(((hexlify(titleId).decode()) + ': ' + (hexlify(decryptedTitleKey)).decode()))
            raise SystemExit(0)

#if args for deckeys or checkbin weren't used above, remaining functions require 3 args minimum
if len(sys.argv) < 3:
    SystemUsage()

# default values
titleid = sys.argv[1]
titlekey = sys.argv[2]
forceDownload = 0
forceDecrypt = 0
make3ds = 1
makecia = 1
nohash = 0
checkKey = 0
checkTempOut = None

# check args
for i in range(len(sys.argv)):
    if sys.argv[i] == '-redown': forceDownload = 1
    elif sys.argv[i] == '-redec': forceDecrypt = 1
    elif sys.argv[i] == '-no3ds': make3ds = 0
    elif sys.argv[i] == '-nocia': makecia = 0
    elif sys.argv[i] == '-check': checkKey = 1

if len(titleid) != 16 or len(titlekey) != 32:
    print('Invalid arguments')
    raise SystemExit(0)

# set CDN default URL
baseurl = 'http://nus.cdn.c.shop.nintendowifi.net/ccs/download/' + titleid

# download tmd and set to 'tmd' object
try:
    tmd = urllib.request.urlopen(baseurl + '/tmd')
except urllib.error.URLError as e:
    print('ERROR: Bad title ID?')
    raise SystemExit(0)
tmd = tmd.read()

#create folder
mkdir_p(titleid)

# https://www.3dbrew.org/wiki/Title_metadata#Signature_Data
if bytes('\x00\x01\x00\x04', 'UTF-8') not in tmd[:4]:
    print('Unexpected signature type.')
    raise SystemExit(0)

# If not normal application, don't make 3ds
if titleid[:8] != '00040000':
    make3ds = 0

# Check OS, path, and current dir to set makerom location
if 'Windows' in platform.system():
    if os.path.isfile('makerom.exe'):
        makerom_command = 'makerom.exe'
    else:
        makerom_command = which('makerom.exe')
else:
    if os.path.isfile('makerom'):
        makerom_command = './makerom'
    else:
        makerom_command = which('makerom')

if makerom_command == None:
    print('Could not find makerom!')
    raise SystemExit(0)

# Set Proper CommonKey ID
if unpack('>H', tmd[0x18e:0x190])[0] & 0x10 == 0x10:
    ckeyid = 1
else:
    ckeyid = 0

# Set Proper Version
version = unpack('>H', tmd[0x1dc:0x1de])[0]

# Set Save Size
saveSize = (unpack('<I', tmd[0x19a:0x19e])[0])/1024

# If DLC Set DLC flag
dlcflag = ''
if titleid[:8] == '0004008c':
    dlcflag = '-dlc'

contentCount = unpack('>H', tmd[0x206:0x208])[0]

# If not normal application, don't make 3ds
if contentCount > 8:
    make3ds = 0

command_cID = []

# Download Contents
fSize = 16*1024
for i in range(contentCount):
    cOffs = 0xB04+(0x30*i)
    cID = format(unpack('>I', tmd[cOffs:cOffs+4])[0], '08x')
    cIDX = format(unpack('>H', tmd[cOffs+4:cOffs+6])[0], '04x')
    cSIZE = format(unpack('>Q', tmd[cOffs+8:cOffs+16])[0], 'd')
    cHASH = tmd[cOffs+16:cOffs+48]
    # If not normal application, don't make 3ds
    if unpack('>H', tmd[cOffs+4:cOffs+6])[0] >= 8:
        make3ds = 0

    print('Content ID:    ' + cID)
    print('Content Index: ' + cIDX)
    print('Content Size:  ' + cSIZE)
    print('Content Hash:  ' + (hexlify(cHASH)).decode())

    # set output location to a folder named for title id and contentid.dec as the file
    outfname = titleid + '/' + cID + '.dec'

    if checkKey == 1:
        print('Downloading and decrypting the first 272 bytes of ' + cID + ' for key check')
        # use range requests to download bytes 0 through 271, needed 272 instead of 260 because AES-128-CBC encrypts in chunks of 128 bits
        try:
            checkReq = urllib.request.Request('%s/%s'%(baseurl, cID))
            checkReq.headers['Range'] = 'bytes=%s-%s' % (0, 271)
            checkTemp = urllib.request.urlopen(checkReq)
        except urllib.error.URLError as e:
            print('ERROR: Possibly wrong container?\n')
            continue

        # set IV to offset 0xf0 length 0x10 of ciphertext; thanks to yellows8 for the offset
        checkTempPerm = checkTemp.read()
        decryptor = AES.new(unhexlify(titlekey), AES.MODE_CBC, checkTempPerm[0xf0:0x100])

        # check for magic ('NCCH') at offset 0x100 length 0x104 of the decrypted content
        checkTempOut = decryptor.decrypt(checkTempPerm)[0x100:0x104]

        if 'NCCH' not in checkTempOut.decode('UTF-8', 'ignore'):
            print('\nERROR: Decryption failed; invalid titlekey')
            raise SystemExit(0)

        print('Titlekey successfully verified to match title ID ' + titleid)
        raise SystemExit(0)

    # if the content location does not exist, redown is set, or the size is incorrect redownload
    if os.path.exists(outfname) == 0 or forceDownload == 1 or os.path.getsize(outfname) != unpack('>Q', tmd[cOffs+8:cOffs+16])[0]:
        response = urllib.request.urlopen(baseurl + '/' + cID)
        chunk_read(response, outfname, unhexlify(titlekey), cIDX + '0000000000000000000000000000', report_hook=chunk_report)

    # check hash and NCCH of downloaded content
    with open(outfname,'rb') as fh:
        fh.seek(0, os.SEEK_END)
        fhSize = fh.tell()
        if fh.tell() != unpack('>Q', tmd[cOffs+8:cOffs+16])[0]:
            print('Title size mismatch.  Download likely incomplete')
            print('Downloaded: ' + format(fh.tell(), 'd'))
            raise SystemExit(0)
        fh.seek(0)
        hash = sha256()

        while fh.tell() != fhSize:
            hash.update(fh.read(0x1000000))
            print('Checking Hash: ' + format(float(fh.tell()*100)/fhSize,'.1f') + '% done\r', end=' ')

        sha256file = hash.hexdigest()
        if sha256file != (hexlify(cHASH)).decode():
            print('hash mismatched, Decryption likely failed, wrong key or file modified?')
            print('got hash: ' + sha256file)
            raise SystemExit(0)
        print('Hash verified successfully.')
        fh.seek(0x100)
        if (fh.read(4)).decode('UTF-8', 'ignore') != 'NCCH':
            makecia = 0
            make3ds = 0
            fh.seek(0x60)
            if fh.read(4) != 'WfA\0':
                print('Not NCCH, nor DSiWare, file likely corrupted')
                raise SystemExit(0)
            else:
                print('Not an NCCH container, likely DSiWare')
        fh.seek(0, os.SEEK_END)
        fSize += fh.tell()

    print('\n')
    command_cID = command_cID + ['-i', outfname + ':0x' + cIDX + ':0x' + cID]

print('\n')
print('The NCCH on eShop games is encrypted and cannot be used')
print('without decryption on a 3DS. To fix this you should copy')
print('all .dec files in the Title ID folder to \'/D9Game/\'')
print('on your SD card, then use the following option in Decrypt9:')
print('\n')
print('\'Game Decryptor Options\' > \'NCCH/NCSD Decryptor\'')
print('\n')
print('Once you have decrypted the files, copy the .dec files from')
print('\'/D9Game/\' back into the Title ID folder, overwriting them.')
print('\n')
input('Press Enter once you have done this...')

# Create RSF File
romrsf = 'Option:\n  MediaFootPadding: true\n  EnableCrypt: false\nSystemControlInfo:\n  SaveDataSize: $(SaveSize)K'
with open('rom.rsf', 'wb') as fh:
    fh.write(romrsf.encode())

#set makerom command with subproces, removing '' if dlcflag isn't set (otherwise makerom breaks)
dotcia_command_array = ([makerom_command, '-f', 'cia', '-rsf', 'rom.rsf', '-o', titleid + '.cia', '-ckeyid', str(ckeyid), '-major', str((version & 0xfc00) >> 10), '-minor', str((version & 0x3f0) >> 4), '-micro', str(version & 0xF), '-DSaveSize=' + str(saveSize), str(dlcflag)] + command_cID)
dot3ds_command_array = ([makerom_command, '-f', 'cci', '-rsf', 'rom.rsf', '-nomodtid', '-o', titleid + '.3ds', '-ckeyid', str(ckeyid), '-major', str((version & 0xfc00) >> 10), '-minor', str((version & 0x3f0) >> 4), '-micro', str(version & 0xF), '-DSaveSize=' + str(saveSize), str(dlcflag)] + command_cID)
if '' in dotcia_command_array:
    dotcia_command_array.remove('')
if '' in dot3ds_command_array:
    dot3ds_command_array.remove('')

if makecia == 1:
    print('\nBuilding ' + titleid + '.cia...')
    call(dotcia_command_array, stderr=STDOUT)

if make3ds == 1:
    print('\nBuilding ' + titleid + '.3ds...')
    call(dot3ds_command_array, stderr=STDOUT)

os.remove('rom.rsf')

if not os.path.isfile(titleid + '.cia') and makecia == 1:
    print('Something went wrong.')
    raise SystemExit(0)

if not os.path.isfile(titleid + '.3ds') and make3ds == 1:
    print('Something went wrong.')
    raise SystemExit(0)

print('Done!')