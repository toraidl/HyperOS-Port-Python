#!/usr/bin/env python
# -*- coding: utf-8 -*-
#====================================================
#          FILE: sdat2img.py
#       AUTHORS: xpirt - luxi78 - howellzhu
#====================================================

import sys, os

def run_sdat2img(transfer_list_file, new_dat_file, output_image_file):
    BLOCK_SIZE = 4096
    
    try:
        trans_list = open(transfer_list_file, 'r')
        version_line = trans_list.readline()
        if not version_line:
             return False
        version = int(version_line)
        
        new_blocks = int(trans_list.readline())
        
        if version >= 2:
            trans_list.readline() # Stash entries
            if version >= 3:
                trans_list.readline() # Max stash size?
                
    except ValueError:
        print('sdat2img: invalid transfer list file')
        return False
    except IndexError:
        print('sdat2img: invalid format')
        return False

    with open(output_image_file, 'wb') as output_img, open(new_dat_file, 'rb') as new_dat:
        for line in trans_list:
            line = line.strip()
            if not line: continue
            
            split = line.split(' ')
            cmd = split[0]
            
            if cmd == 'new':
                # Format: new range_set
                # range_set: count, start, end, start, end...
                try:
                    params = [int(x) for x in split[1].split(',')]
                    # The first number is the number of pairs that follow.
                    # e.g., "2,10,12" -> 1 range [10, 12) (2 blocks)
                    # "4,10,12,15,17" -> 2 ranges: [10, 12) and [15, 17)
                    
                    ranges = params[1:]
                    
                    for i in range(0, len(ranges), 2):
                        start = ranges[i]
                        end = ranges[i+1]
                        block_count = end - start
                        
                        output_img.seek(start * BLOCK_SIZE)
                        
                        # Read exactly block_count * BLOCK_SIZE
                        data = new_dat.read(block_count * BLOCK_SIZE)
                        output_img.write(data)
                        
                except (ValueError, IndexError) as e:
                    print(f"sdat2img: error parsing line: {line} ({e})")
                    return False

    trans_list.close()
    return True

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: sdat2img.py <transfer_list> <system_new_dat> <system_img>")
        sys.exit(1)
    
    success = run_sdat2img(sys.argv[1], sys.argv[2], sys.argv[3])
    if success:
        print(f"Done! Output image: {sys.argv[3]}")
    else:
        sys.exit(1)
