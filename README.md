# Wuwa-RabbitFX-Applier
<img width="2198" height="1179" alt="image" src="https://github.com/user-attachments/assets/16cb079f-0bf8-4039-8853-6d686374ffad" />
This is a tool to make the proposed fix from [tutorial](https://gamebanana.com/tuts/20005) a lot faster.

Usage: 
Select the ini of the mod you want to fix
Select a target Component from the list on the left side
look through the DDS files via the TextureOverride Inspector List
<img width="2193" height="1173" alt="image" src="https://github.com/user-attachments/assets/e627dfb5-f9f8-43db-8315-839bbe972e26" />
Add the diffuse Lightmap and normal map if present 
Change every non yoused source section to none 
<img width="494" height="480" alt="image" src="https://github.com/user-attachments/assets/0a51fb9b-9040-4017-9365-93dc80e17f99" />

Click apply patch and check if it fixed it if not you can roll back via restore last backup

For complexer mods like the ones made by yyzj and some other cases:
You need to use ps slot mapping instead of RabbitFx Channels for some bodyparts of some mods like the face.

<img width="472" height="736" alt="image" src="https://github.com/user-attachments/assets/798651a4-8ead-49bf-a70b-829ec1dbab89" />

You will need to play around with the slot to find the right one 

i recommend to read the tutorial first but even with it, it will be a lot of trial and error 
Sorry if it is a bit buggy but i hope it helps a few people

